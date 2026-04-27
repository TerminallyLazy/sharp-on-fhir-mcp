"""Tiny pure-Python helpers for normalizing FHIR R4 resources.

These helpers convert raw FHIR JSON into compact, LLM-friendly dicts that
preserve the most useful fields without the verbose nesting. Every helper
is null-safe and never raises on partial / malformed resources — clinical
data in the wild rarely conforms perfectly to the spec.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

# --
# Bundle helpers
# --


def iter_bundle_resources(bundle: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield each ``resource`` from a FHIR ``Bundle.entry``."""
    for entry in (bundle or {}).get("entry") or []:
        resource = entry.get("resource")
        if resource:
            yield resource


def bundle_to_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Eager version of :func:`iter_bundle_resources`."""
    return list(iter_bundle_resources(bundle))


def bundle_total(bundle: dict[str, Any]) -> int:
    """Best-effort total count from a Bundle (falls back to entry length)."""
    if not isinstance(bundle, dict):
        return 0
    total = bundle.get("total")
    if isinstance(total, int):
        return total
    return len(bundle.get("entry") or [])


def bundle_next_link(bundle: dict[str, Any]) -> str | None:
    """Return the ``next`` link from a Bundle, if present."""
    for link in (bundle or {}).get("link") or []:
        if link.get("relation") == "next":
            return link.get("url")
    return None


# --
# Codeable concept / coding
# --


def coding_text(concept: dict[str, Any] | None) -> str:
    """Render a CodeableConcept as a short, human-readable label."""
    if not concept:
        return ""
    if concept.get("text"):
        return str(concept["text"])
    for c in concept.get("coding") or []:
        display = c.get("display")
        if display:
            return str(display)
        code = c.get("code")
        if code:
            return str(code)
    return ""


def first_coding(concept: dict[str, Any] | None) -> dict[str, Any]:
    """Return the first ``coding`` entry inside a CodeableConcept (or {})."""
    if not concept:
        return {}
    codings = concept.get("coding") or []
    return codings[0] if codings else {}


def category_codes(resource: dict[str, Any]) -> list[str]:
    """Flatten ``resource.category`` (list of CodeableConcept) into codes."""
    out: list[str] = []
    for cat in resource.get("category") or []:
        for c in cat.get("coding") or []:
            code = c.get("code")
            if code:
                out.append(code)
    return out


# --
# Patient
# --


def humanize_name(name: dict[str, Any] | None) -> str:
    """Format a HumanName as ``Given Family``."""
    if not name:
        return ""
    if name.get("text"):
        return str(name["text"])
    given = " ".join(name.get("given") or [])
    family = name.get("family") or ""
    return " ".join(p for p in (given, family) if p)


def patient_display_name(patient: dict[str, Any]) -> str:
    """Pick the best HumanName for a Patient."""
    names = patient.get("name") or []
    if not names:
        return f"Patient/{patient.get('id', '')}".rstrip("/")
    # Prefer ``official`` use, then ``usual``, then anything.
    by_use = {n.get("use", ""): n for n in names}
    chosen = by_use.get("official") or by_use.get("usual") or names[0]
    return humanize_name(chosen) or f"Patient/{patient.get('id', '')}"


def patient_phone(patient: dict[str, Any]) -> str | None:
    for telecom in patient.get("telecom") or []:
        if telecom.get("system") == "phone" and telecom.get("value"):
            return str(telecom["value"])
    return None


def patient_email(patient: dict[str, Any]) -> str | None:
    for telecom in patient.get("telecom") or []:
        if telecom.get("system") == "email" and telecom.get("value"):
            return str(telecom["value"])
    return None


def patient_address(patient: dict[str, Any]) -> str | None:
    addresses = patient.get("address") or []
    if not addresses:
        return None
    addr = addresses[0]
    if addr.get("text"):
        return str(addr["text"])
    parts: list[str] = []
    line = addr.get("line") or []
    if line:
        parts.append(", ".join(line))
    city_state = " ".join(p for p in (addr.get("city"), addr.get("state")) if p)
    if city_state:
        parts.append(city_state)
    if addr.get("postalCode"):
        parts.append(str(addr["postalCode"]))
    return ", ".join(parts) if parts else None


def calculate_age(birth_date: str | None) -> int | None:
    """Compute age in years from a YYYY[-MM[-DD]] birthDate string."""
    if not birth_date:
        return None
    try:
        # Pad partial dates to YYYY-01-01 / YYYY-MM-01
        parts = birth_date.split("-")
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2][:2]) if len(parts) > 2 else 1
        bd = datetime(year, month, day)
    except (ValueError, IndexError):
        return None
    today = datetime.now()
    age = today.year - bd.year
    if (today.month, today.day) < (bd.month, bd.day):
        age -= 1
    return age


def patient_summary(patient: dict[str, Any]) -> dict[str, Any]:
    """Return a compact dict of the most-useful Patient fields."""
    return {
        "id": patient.get("id"),
        "name": patient_display_name(patient),
        "gender": patient.get("gender"),
        "date_of_birth": patient.get("birthDate"),
        "age": calculate_age(patient.get("birthDate")),
        "phone": patient_phone(patient),
        "email": patient_email(patient),
        "address": patient_address(patient),
        "active": patient.get("active"),
    }


# --
# Observation
# --


def observation_value(obs: dict[str, Any]) -> tuple[Any, str | None]:
    """Extract the most likely numeric/string value + unit from an Observation."""
    qty = obs.get("valueQuantity")
    if qty:
        return qty.get("value"), qty.get("unit") or qty.get("code")
    if "valueString" in obs:
        return obs["valueString"], None
    if "valueBoolean" in obs:
        return obs["valueBoolean"], None
    if "valueInteger" in obs:
        return obs["valueInteger"], None
    cc = obs.get("valueCodeableConcept")
    if cc:
        return coding_text(cc), None
    rng = obs.get("valueRange")
    if rng:
        low = (rng.get("low") or {}).get("value")
        high = (rng.get("high") or {}).get("value")
        return f"{low}–{high}", (rng.get("low") or {}).get("unit")
    return None, None


def observation_reference_range(obs: dict[str, Any]) -> str | None:
    """Format the first referenceRange as ``low–high unit``."""
    ranges = obs.get("referenceRange") or []
    if not ranges:
        return None
    rng = ranges[0]
    if rng.get("text"):
        return str(rng["text"])
    low = (rng.get("low") or {}).get("value")
    high = (rng.get("high") or {}).get("value")
    unit = (rng.get("low") or {}).get("unit") or (rng.get("high") or {}).get("unit") or ""
    if low is not None and high is not None:
        return f"{low}–{high} {unit}".strip()
    if low is not None:
        return f">{low} {unit}".strip()
    if high is not None:
        return f"<{high} {unit}".strip()
    return None


def observation_is_abnormal(obs: dict[str, Any]) -> bool:
    """Best-effort abnormality flag using FHIR ``interpretation``."""
    abnormal_codes = {"H", "L", "HH", "LL", "A", "AA", "HU", "LU"}
    for interp in obs.get("interpretation") or []:
        for c in interp.get("coding") or []:
            if c.get("code") in abnormal_codes:
                return True
    return False


def observation_summary(obs: dict[str, Any]) -> dict[str, Any]:
    """Compact Observation summary safe for tool output."""
    value, unit = observation_value(obs)
    return {
        "id": obs.get("id"),
        "test": coding_text(obs.get("code")),
        "loinc": _first_loinc(obs.get("code") or {}),
        "value": value,
        "unit": unit,
        "normal_range": observation_reference_range(obs),
        "abnormal": observation_is_abnormal(obs),
        "categories": category_codes(obs),
        "date": obs.get("effectiveDateTime")
        or (obs.get("effectivePeriod") or {}).get("start")
        or obs.get("issued"),
        "status": obs.get("status"),
    }


def _first_loinc(concept: dict[str, Any]) -> str | None:
    for c in concept.get("coding") or []:
        if c.get("system") == "http://loinc.org" and c.get("code"):
            return str(c["code"])
    return None


# --
# Condition / Allergy / Immunization / Medication
# --


def condition_summary(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "name": coding_text(c.get("code")),
        "icd_code": _first_icd(c.get("code") or {}),
        "snomed": _first_snomed(c.get("code") or {}),
        "clinical_status": coding_text(c.get("clinicalStatus")),
        "verification_status": coding_text(c.get("verificationStatus")),
        "onset_date": c.get("onsetDateTime")
        or (c.get("onsetPeriod") or {}).get("start"),
        "recorded_date": c.get("recordedDate"),
        "category": coding_text((c.get("category") or [{}])[0])
        if c.get("category")
        else None,
        "severity": coding_text(c.get("severity")),
    }


def _first_icd(concept: dict[str, Any]) -> str | None:
    for c in concept.get("coding") or []:
        sys = c.get("system") or ""
        if "icd-10" in sys.lower() or "icd-9" in sys.lower():
            return str(c.get("code") or "")
    return None


def _first_snomed(concept: dict[str, Any]) -> str | None:
    for c in concept.get("coding") or []:
        if c.get("system") == "http://snomed.info/sct":
            return str(c.get("code") or "")
    return None


def allergy_summary(a: dict[str, Any]) -> dict[str, Any]:
    reactions: list[str] = []
    severity: str | None = None
    for r in a.get("reaction") or []:
        for m in r.get("manifestation") or []:
            t = coding_text(m)
            if t:
                reactions.append(t)
        severity = severity or r.get("severity")
    return {
        "id": a.get("id"),
        "allergen": coding_text(a.get("code")),
        "type": a.get("type"),
        "category": (a.get("category") or [None])[0],
        "criticality": a.get("criticality"),
        "clinical_status": coding_text(a.get("clinicalStatus")),
        "verification_status": coding_text(a.get("verificationStatus")),
        "reaction": ", ".join(reactions) if reactions else None,
        "severity": severity,
        "onset_date": a.get("onsetDateTime"),
        "recorded_date": a.get("recordedDate"),
    }


def medication_request_summary(m: dict[str, Any]) -> dict[str, Any]:
    med = m.get("medicationCodeableConcept") or {}
    if not med and m.get("medicationReference"):
        med = {"text": m["medicationReference"].get("display") or ""}
    dose = ""
    di = (m.get("dosageInstruction") or [{}])[0]
    if di.get("text"):
        dose = di["text"]
    else:
        try:
            dq = (di.get("doseAndRate") or [{}])[0].get("doseQuantity") or {}
            dose = f"{dq.get('value', '')} {dq.get('unit', '')}".strip()
        except (IndexError, AttributeError):
            dose = ""
    timing = ((di.get("timing") or {}).get("code") or {}).get("text") or coding_text(
        (di.get("timing") or {}).get("code")
    )
    return {
        "id": m.get("id"),
        "name": coding_text(med),
        "rxnorm": _first_rxnorm(med),
        "dose": dose,
        "frequency": timing,
        "route": coding_text(di.get("route")),
        "status": m.get("status"),
        "intent": m.get("intent"),
        "authored_on": m.get("authoredOn"),
        "requester": (m.get("requester") or {}).get("display"),
    }


def _first_rxnorm(concept: dict[str, Any]) -> str | None:
    for c in concept.get("coding") or []:
        if c.get("system") == "http://www.nlm.nih.gov/research/umls/rxnorm":
            return str(c.get("code") or "")
    return None


def immunization_summary(i: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": i.get("id"),
        "vaccine": coding_text(i.get("vaccineCode")),
        "cvx_code": _first_cvx(i.get("vaccineCode") or {}),
        "status": i.get("status"),
        "date_administered": i.get("occurrenceDateTime"),
        "lot_number": i.get("lotNumber"),
    }


def _first_cvx(concept: dict[str, Any]) -> str | None:
    for c in concept.get("coding") or []:
        sys = c.get("system") or ""
        if sys.endswith("cvx") or "cvx" in sys.lower():
            return str(c.get("code") or "")
    return None


def diagnostic_report_summary(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r.get("id"),
        "name": coding_text(r.get("code")),
        "category": coding_text((r.get("category") or [{}])[0])
        if r.get("category")
        else None,
        "status": r.get("status"),
        "date": r.get("effectiveDateTime")
        or (r.get("effectivePeriod") or {}).get("start")
        or r.get("issued"),
        "conclusion": r.get("conclusion"),
        "result_count": len(r.get("result") or []),
    }


def encounter_summary(e: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": e.get("id"),
        "status": e.get("status"),
        "class": (e.get("class") or {}).get("display")
        or (e.get("class") or {}).get("code"),
        "type": coding_text((e.get("type") or [{}])[0]) if e.get("type") else None,
        "reason": coding_text((e.get("reasonCode") or [{}])[0])
        if e.get("reasonCode")
        else None,
        "start": (e.get("period") or {}).get("start"),
        "end": (e.get("period") or {}).get("end"),
        "service_provider": (e.get("serviceProvider") or {}).get("display"),
    }


def appointment_summary(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": a.get("id"),
        "status": a.get("status"),
        "service_type": coding_text((a.get("serviceType") or [{}])[0])
        if a.get("serviceType")
        else None,
        "appointment_type": coding_text(a.get("appointmentType")),
        "reason": coding_text((a.get("reasonCode") or [{}])[0])
        if a.get("reasonCode")
        else None,
        "description": a.get("description"),
        "start": a.get("start"),
        "end": a.get("end"),
        "minutes_duration": a.get("minutesDuration"),
        "comment": a.get("comment"),
    }


def document_reference_summary(d: dict[str, Any]) -> dict[str, Any]:
    attachments: list[dict[str, Any]] = []
    for content in d.get("content") or []:
        att = content.get("attachment") or {}
        if att.get("url") or att.get("data"):
            attachments.append(
                {
                    "url": att.get("url"),
                    "content_type": att.get("contentType"),
                    "title": att.get("title"),
                    "size": att.get("size"),
                }
            )
    return {
        "id": d.get("id"),
        "type": coding_text(d.get("type")),
        "category": coding_text((d.get("category") or [{}])[0])
        if d.get("category")
        else None,
        "status": d.get("status"),
        "doc_status": d.get("docStatus"),
        "date": d.get("date"),
        "description": d.get("description"),
        "attachments": attachments,
    }


def coverage_summary(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "status": c.get("status"),
        "type": coding_text(c.get("type")),
        "subscriber_id": c.get("subscriberId"),
        "payor": (c.get("payor") or [{}])[0].get("display")
        if c.get("payor")
        else None,
        "period_start": (c.get("period") or {}).get("start"),
        "period_end": (c.get("period") or {}).get("end"),
    }


__all__ = [
    "iter_bundle_resources",
    "bundle_to_resources",
    "bundle_total",
    "bundle_next_link",
    "coding_text",
    "first_coding",
    "category_codes",
    "humanize_name",
    "patient_display_name",
    "patient_phone",
    "patient_email",
    "patient_address",
    "calculate_age",
    "patient_summary",
    "observation_value",
    "observation_reference_range",
    "observation_is_abnormal",
    "observation_summary",
    "condition_summary",
    "allergy_summary",
    "medication_request_summary",
    "immunization_summary",
    "diagnostic_report_summary",
    "encounter_summary",
    "appointment_summary",
    "document_reference_summary",
    "coverage_summary",
]
