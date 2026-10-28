"""Minimum information (docs/minimum-information.md) checked in code. No model: request facts in, gaps out."""

import re
from decimal import Decimal, InvalidOperation

# Label variants per invoice field; the first match in the text wins, so original facts beat later additions.
INVOICE_FIELDS = {
    "provider_id": r"provider\s*(?:id|code)\s*[:#]\s*([^\s,;]+)",
    "invoice_number": r"invoice\s*(?:number|no\b\.?|#)\s*[:#]?\s*([^\s,;]+)",
    "invoice_date": r"(?:invoice\s*date|date\s*of\s*invoice)\s*:\s*(\d{4}-\d{2}-\d{2})",
    "service_date": r"(?:service\s*date|date\s*of\s*service|treatment\s*date)\s*:\s*(\d{4}-\d{2}-\d{2})",
    "patient": r"patient(?:\s*name)?\s*:\s*([^\n.;]+)",
    "service": r"(?:^|\n)\s*(?:service|treatment|description)\s*:\s*([^\n]+)",
    "amount": r"(?:amount|total(?:\s*amount)?)\s*:\s*(?:EUR|€)\s*([0-9][0-9.,]*)",
    "paid": r"(?:payment\s*status|payment|status)\s*:\s*([^\n]+)",
}
LABELS = {
    "invoice": "the paid invoice text (provider ID, invoice number, invoice date, service date, patient, "
               "service description, EUR amount and payment status)",
    "provider_id": "the provider ID", "invoice_number": "the invoice number", "invoice_date": "the invoice date",
    "service_date": "the service date", "patient": "the patient's name", "service": "the service description",
    "amount": "the invoice amount in EUR", "paid": "confirmation on the invoice that it has been paid",
    "prescription": "the prescription text, naming the patient and the prescribed service",
    "prescription_contents": "a prescription that names the same patient as the invoice and the prescribed service",
}
# Excluded services first: "dental cleaning" must not read as a covered service.
SERVICES = (
    ("dental_care", r"\b(dental|dentist|teeth|tooth|orthodont)"),
    ("cosmetic_treatment", r"\b(cosmetic|aesthetic|botox)"),
    ("medicines", r"\b(medicines?|medications?|pharmacy|drugs?)\b"),
    ("physiotherapy", r"physiotherap|physical\s+therapy"),
    ("diagnostic_test", r"\b(diagnostic|blood\s+test|mri|x-ray|scan|ultrasound|laboratory|lab\s+test)"),
    ("specialist_consultation", r"\b(specialist|consultation|cardiolog|dermatolog|visit)"),
)
EXCLUDED = ("dental_care", "cosmetic_treatment", "medicines")
PRESCRIPTION_SERVICES = ("diagnostic_test", "physiotherapy")
NOT_PAID = r"\b(unpaid|not\s+paid|outstanding|due|pending)\b"


def amount(value):
    """'1,925.00' or '1925,00' -> '1925.00'; None if it is not a money amount."""
    value = value.rstrip(".,")
    if "," in value and "." in value:
        value = value.replace(",", "")
    elif "," in value:
        value = value.replace(",", ".")
    try:
        return f"{Decimal(value).quantize(Decimal('0.01')):.2f}"
    except InvalidOperation:
        return None


def parse_invoice(text):
    facts = {}
    for field, pattern in INVOICE_FIELDS.items():
        match = re.search(pattern, text or "", re.I)
        facts[field] = match.group(1).strip() if match else None
    if facts["amount"]:
        facts["amount"] = amount(facts["amount"])
    if facts["paid"] and (re.search(NOT_PAID, facts["paid"], re.I) or not re.search(r"\bpaid\b", facts["paid"], re.I)):
        facts["paid"] = None  # A payment status that does not say paid is not proof of payment.
    return facts


def service_category(text):
    for category, pattern in SERVICES:
        if re.search(pattern, text or "", re.I):
            return category
    return None


def requested_service(facts):
    """The invoice's service line first; the request text only when no invoice says what was done."""
    return service_category(parse_invoice(facts.get("invoice_text"))["service"]) or service_category(facts.get("text"))


def excluded(facts):
    return requested_service(facts) in EXCLUDED


def prescription_ok(text, patient, category):
    names_patient = patient is None or patient.lower() in (text or "").lower()
    return names_patient and service_category(text) == category


def missing(facts):
    """Everything a covered reimbursement still lacks, for the one clarification. Excluded services need nothing."""
    if excluded(facts):
        return []
    if not (facts.get("invoice_text") or "").strip():
        gaps = ["invoice"]
        invoice = {}
    else:
        invoice = parse_invoice(facts["invoice_text"])
        gaps = [field for field, value in invoice.items() if value is None]
    category = requested_service(facts)
    if category in PRESCRIPTION_SERVICES:
        prescription = (facts.get("prescription_text") or "").strip()
        if not prescription:
            gaps.append("prescription")
        elif not prescription_ok(prescription, invoice.get("patient"), category):
            gaps.append("prescription_contents")
    return gaps


def build_question(gaps):
    items = [LABELS[gap] for gap in gaps]
    listed = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
    return (f"To process your reimbursement, please send {listed}. "
            "Please include documents as text in the invoice_text / prescription_text fields.")


def invoice_mismatches(args, facts):
    """create_claim arguments that disagree with the supplied invoice text. Unparsed fields are not compared."""
    invoice, sent = parse_invoice(facts.get("invoice_text")), args["invoice"]
    expected = {"provider_id": (invoice["provider_id"], sent["provider_id"]),
                "invoice_number": (invoice["invoice_number"], sent["invoice_number"]),
                "invoice_date": (invoice["invoice_date"], sent["invoice_date"]),
                "service_date": (invoice["service_date"], sent["service_date"]),
                "expense": (invoice["amount"], sent["expense"]),
                "patient_name": (invoice["patient"], args["patient_name"]),
                "service_category": (service_category(invoice["service"]), args["service_category"])}
    return {field: found for field, (found, used) in expected.items()
            if found is not None and str(found).lower() != str(used).lower()}


def conflicts(facts, reply):
    """Invoice facts a reply would change. The original value is kept; the caller logs the conflict."""
    before, after = parse_invoice(facts.get("invoice_text")), parse_invoice(reply.get("invoice_text"))
    return [field for field, value in after.items() if value is not None and before[field] not in (None, value)]


def unsupported_contact_values(changes, facts):
    """Contact values that appear neither in the request nor in the reply: the model must not supply them."""
    text = f"{facts.get('text', '')}\n{facts.get('clarification_text', '')}"
    digits = re.sub(r"\D", "", text)
    values = {key: value for key, value in changes.items() if key != "address"}
    values.update({f"address.{key}": value for key, value in (changes.get("address") or {}).items()})
    return [key for key, value in values.items()
            if str(value).lower() not in text.lower()
            and not (key == "phone" and re.sub(r"\D", "", value) in digits)]
