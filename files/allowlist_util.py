"""Plate normalisation helpers for allowlist matching."""

# Maps visually confusable OCR characters to a canonical form.
# Applied to both the candidate plate and every allowlist entry before comparison
# so that e.g. "AK750CM" and "AK75OCM" resolve to the same key.
_CONFUSABLES = str.maketrans("OI1S5B8Z2", "011558822")


def normalise_plate(plate: str) -> str:
    """Upper-case and fold common OCR confusable characters."""
    return "".join(plate.split()).upper().translate(_CONFUSABLES)


def group_allowlist(data):
    """Present supported legacy file formats without rewriting their contents."""
    owners = {}
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and "owner" in item:
            owner = item["owner"]
            plates = item.get("plates", [item["plate"]] if "plate" in item else [])
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            plates, owner = [item[0]], item[1]
        else:
            continue
        if isinstance(plates, str):
            plates = [plates]
        if not isinstance(owner, str) or not isinstance(plates, list):
            continue
        owners.setdefault(owner, []).extend(plate for plate in plates if isinstance(plate, str) and plate.strip())
    return [{"owner": owner, "plates": sorted(set(plates))} for owner, plates in owners.items()]


def validate_allowlist(data):
    """Validate a complete replacement without changing the saved list.

    Keep presentation spacing for display, but compare identities using the
    same whitespace/confusable rules as the worker. Reject ambiguous entries
    instead of silently selecting an owner according to row order.
    """
    if not isinstance(data, list):
        raise ValueError("Expected a list of owners and plates")
    cleaned = []
    registered = {}
    for index, item in enumerate(data, start=1):
        prefix = f"Row {index}: "
        if isinstance(item, dict) and "owner" in item and ("plates" in item or "plate" in item):
            owner = item["owner"]
            plates = item.get("plates") if "plates" in item else [item["plate"]]
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            plates, owner = [item[0]], item[1]
        else:
            raise ValueError(prefix + "expected an owner and plates")
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError(prefix + "owner required")
        if isinstance(plates, str):
            plates = [plates]
        if not isinstance(plates, list) or not plates:
            raise ValueError(prefix + "at least one plate is required")
        display_plates = []
        row_identities = set()
        for plate in plates:
            if not isinstance(plate, str):
                raise ValueError(prefix + "plates must be text")
            display = plate.strip().upper()
            compact = "".join(display.split())
            if not compact or len(compact) > 32 or not compact.isascii() or not compact.isalnum():
                raise ValueError(prefix + "plates must contain letters and numbers, with optional spaces")
            if compact in row_identities:
                continue
            key = normalise_plate(display)
            if key in registered:
                previous_row, previous_plate = registered[key]
                raise ValueError(
                    prefix + f"{display} conflicts with {previous_plate} in row {previous_row} "
                    "after OCR character correction"
                )
            registered[key] = (index, display)
            row_identities.add(compact)
            display_plates.append(display)
        cleaned.append({"owner": owner.strip(), "plates": sorted(display_plates)})
    return cleaned
