from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


NAME_RE = re.compile(r"^[a-z0-9_]{1,512}$")
LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:_[A-Z]{2})?$")
POSITIONAL_RE = re.compile(r"\{\{(\d+)\}\}")
NAMED_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")
ANY_PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")


@dataclass(frozen=True)
class TemplateValidationError:
    details: str
    subcode: int | None = None
    title: str = "Invalid message template"


def validate_template(payload: dict[str, Any]) -> TemplateValidationError | None:
    for field in ("name", "language", "category", "components"):
        if field not in payload:
            return TemplateValidationError(f"Parameter {field} is required.")
    if not isinstance(payload["name"], str) or not NAME_RE.fullmatch(payload["name"]):
        return TemplateValidationError(
            "Template name must contain 1 to 512 lowercase letters, numbers, or underscores."
        )
    if not isinstance(payload["language"], str) or not LOCALE_RE.fullmatch(payload["language"]):
        return TemplateValidationError("Template language is not a supported locale identifier.")
    category = str(payload["category"]).upper()
    if category not in {"UTILITY", "MARKETING", "AUTHENTICATION"}:
        return TemplateValidationError(
            "Template category must be UTILITY, MARKETING, or AUTHENTICATION."
        )
    parameter_format = str(payload.get("parameter_format", "POSITIONAL")).upper()
    if parameter_format not in {"POSITIONAL", "NAMED"}:
        return TemplateValidationError("parameter_format must be POSITIONAL or NAMED.")
    components = payload["components"]
    if not isinstance(components, list) or not components:
        return TemplateValidationError("components must be a non-empty array.")
    if not all(isinstance(component, dict) for component in components):
        return TemplateValidationError("Every template component must be an object.")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for component in components:
        component_type = str(component.get("type", "")).upper()
        if component_type not in {"HEADER", "BODY", "FOOTER", "BUTTONS", "CAROUSEL", "LIMITED_TIME_OFFER"}:
            return TemplateValidationError(f"Unsupported template component type {component_type!r}.")
        grouped.setdefault(component_type, []).append(component)
    if len(grouped.get("BODY", [])) != 1:
        return TemplateValidationError("A template must contain exactly one BODY component.")
    for component_type in ("HEADER", "FOOTER", "BUTTONS", "CAROUSEL", "LIMITED_TIME_OFFER"):
        if len(grouped.get(component_type, [])) > 1:
            return TemplateValidationError(f"A template can contain at most one {component_type} component.")

    if category == "AUTHENTICATION":
        return _validate_authentication(grouped)
    if error := _validate_body(grouped["BODY"][0], parameter_format):
        return error
    if grouped.get("HEADER") and (error := _validate_header(grouped["HEADER"][0], parameter_format)):
        return error
    if grouped.get("FOOTER") and (error := _validate_footer(grouped["FOOTER"][0])):
        return error
    if grouped.get("BUTTONS") and (error := _validate_buttons(grouped["BUTTONS"][0])):
        return error
    if grouped.get("LIMITED_TIME_OFFER"):
        if category != "MARKETING":
            return TemplateValidationError("LIMITED_TIME_OFFER is only supported on MARKETING templates.")
        if error := _validate_limited_time_offer(grouped["LIMITED_TIME_OFFER"][0]):
            return error
    if grouped.get("CAROUSEL") and (error := _validate_carousel(grouped["CAROUSEL"][0], parameter_format)):
        return error
    return None


def _variables(text: str, parameter_format: str) -> list[str]:
    regex = NAMED_RE if parameter_format == "NAMED" else POSITIONAL_RE
    return regex.findall(text)


def _validate_placeholder_syntax(text: str, parameter_format: str) -> TemplateValidationError | None:
    expected = NAMED_RE if parameter_format == "NAMED" else POSITIONAL_RE
    for match in ANY_PLACEHOLDER_RE.finditer(text):
        if not expected.fullmatch(match.group(0)):
            return TemplateValidationError(f"Invalid {parameter_format.lower()} parameter {match.group(0)}.")
    variables = _variables(text, parameter_format)
    if variables and (expected.match(text) or expected.search(text.rstrip()) and expected.search(text.rstrip()).end() == len(text.rstrip())):
        return TemplateValidationError(
            "Parameters cannot appear at the beginning or end of component text.", subcode=2388299
        )
    if re.search(r"\}\}\s*\{\{", text):
        return TemplateValidationError("Adjacent template parameters are not allowed.")
    if parameter_format == "POSITIONAL" and variables:
        indexes = {int(value) for value in variables}
        if 0 in indexes or indexes != set(range(1, max(indexes) + 1)):
            return TemplateValidationError("Positional parameters must form a contiguous sequence starting at {{1}}.")
    return None


def _validate_body(body: dict[str, Any], parameter_format: str) -> TemplateValidationError | None:
    text = body.get("text")
    if not isinstance(text, str) or not text:
        return TemplateValidationError("BODY text is required.")
    if len(text) > 1024:
        return TemplateValidationError("BODY text cannot exceed 1024 characters.")
    if error := _validate_placeholder_syntax(text, parameter_format):
        return error
    variables = set(_variables(text, parameter_format))
    if not variables:
        return None
    example = body.get("example")
    if not isinstance(example, dict):
        return TemplateValidationError("BODY parameters require an example.", subcode=2388043)
    if parameter_format == "POSITIONAL":
        samples = example.get("body_text")
        if not (isinstance(samples, list) and len(samples) == 1 and isinstance(samples[0], list)):
            return TemplateValidationError("BODY example.body_text must use the nested [[...]] shape.", subcode=2388043)
        if len(samples[0]) != len(variables):
            return TemplateValidationError("BODY example count must match the number of parameters.")
    else:
        samples = example.get("body_text_named_params")
        if not isinstance(samples, list) or any(not isinstance(item, dict) for item in samples):
            return TemplateValidationError("Named BODY parameters require body_text_named_params examples.")
        names = {item.get("param_name") for item in samples}
        if names != variables or len(samples) != len(variables):
            return TemplateValidationError("Named BODY examples must exactly match the parameters in the text.")
    return None


def _validate_header(header: dict[str, Any], parameter_format: str) -> TemplateValidationError | None:
    header_format = str(header.get("format", "TEXT")).upper()
    if header_format == "TEXT":
        text = header.get("text")
        if not isinstance(text, str) or not text:
            return TemplateValidationError("TEXT HEADER text is required.")
        if len(text) > 60:
            return TemplateValidationError("HEADER text cannot exceed 60 characters.")
        if error := _validate_placeholder_syntax(text, parameter_format):
            return error
        variables = set(_variables(text, parameter_format))
        if len(variables) > 1:
            return TemplateValidationError("A text HEADER can contain at most one parameter.")
        if variables:
            example_key = "header_text_named_params" if parameter_format == "NAMED" else "header_text"
            samples = (header.get("example") or {}).get(example_key)
            if not isinstance(samples, list) or len(samples) != 1:
                return TemplateValidationError(f"Parameterized HEADER requires one {example_key} example.")
        return None
    if header_format in {"IMAGE", "VIDEO", "DOCUMENT"}:
        handles = (header.get("example") or {}).get("header_handle")
        if header.get("text") is not None or not isinstance(handles, list) or len(handles) != 1:
            return TemplateValidationError(f"{header_format} HEADER requires exactly one uploaded header_handle and no text.")
        return None
    if header_format == "LOCATION":
        if header.get("text") is not None:
            return TemplateValidationError("LOCATION HEADER cannot contain text.")
        return None
    return TemplateValidationError(f"Unsupported HEADER format {header_format!r}.")


def _validate_footer(footer: dict[str, Any]) -> TemplateValidationError | None:
    text = footer.get("text")
    if not isinstance(text, str) or not text:
        return TemplateValidationError("FOOTER text is required.")
    if len(text) > 60:
        return TemplateValidationError("FOOTER text cannot exceed 60 characters.")
    if ANY_PLACEHOLDER_RE.search(text):
        return TemplateValidationError("FOOTER cannot contain parameters.")
    return None


WA_LINK_RE = re.compile(r"(?://|^)(?:[\w-]+\.)*(?:wa\.me|whatsapp\.com)(?:[/:?#]|$)", re.IGNORECASE)


def _validate_buttons(component: dict[str, Any]) -> TemplateValidationError | None:
    buttons = component.get("buttons")
    if not isinstance(buttons, list) or not 1 <= len(buttons) <= 10:
        return TemplateValidationError("BUTTONS must contain between 1 and 10 buttons.")
    counts: dict[str, int] = {}
    groups: list[bool] = []
    for button in buttons:
        if not isinstance(button, dict):
            return TemplateValidationError("Every button must be an object.")
        button_type = str(button.get("type", "")).upper()
        counts[button_type] = counts.get(button_type, 0) + 1
        groups.append(button_type == "QUICK_REPLY")
        label = button.get("text")
        if not isinstance(label, str) or not 1 <= len(label) <= 25:
            return TemplateValidationError("Every button label must contain between 1 and 25 characters.")
        if button_type == "URL":
            url = button.get("url")
            if not isinstance(url, str) or not url or len(url) > 2000:
                return TemplateValidationError("URL buttons require a URL of at most 2000 characters.")
            variables = POSITIONAL_RE.findall(url)
            if len(set(variables)) > 1:
                return TemplateValidationError("A URL button supports at most one parameter.")
            if variables and (not isinstance(button.get("example"), list) or len(button["example"]) != 1):
                return TemplateValidationError(
                    "component of type BUTTONS is missing expected field(s) (example)",
                    subcode=2388043,
                    title='Message template "components" param is missing expected field(s)',
                )
            if WA_LINK_RE.search(url):
                return TemplateValidationError(
                    "Direct links to WhatsApp aren't allowed for buttons.",
                    subcode=2388081,
                    title="Error while adding button URL",
                )
        elif button_type == "PHONE_NUMBER":
            phone = button.get("phone_number")
            if not phone:
                return TemplateValidationError("PHONE_NUMBER buttons require phone_number.")
            if not isinstance(phone, str) or len(phone) > 20:
                return TemplateValidationError("PHONE_NUMBER phone_number cannot exceed 20 characters.")
        elif button_type == "COPY_CODE":
            example = button.get("example")
            if not example:
                return TemplateValidationError("COPY_CODE buttons require an example coupon code.")
            code = example[0] if isinstance(example, list) and example else example
            if not isinstance(code, str) or len(code) > 15:
                return TemplateValidationError("COPY_CODE example cannot exceed 15 characters.")
        elif button_type == "FLOW":
            if bool(button.get("flow_id")) == bool(button.get("flow_name")):
                return TemplateValidationError("FLOW buttons require exactly one of flow_id or flow_name.")
            action = str(button.get("flow_action", "navigate")).lower()
            if action not in {"navigate", "data_exchange"}:
                return TemplateValidationError("FLOW flow_action must be navigate or data_exchange.")
            if action == "navigate" and not button.get("navigate_screen"):
                return TemplateValidationError("FLOW buttons using navigate require navigate_screen.")
            if str(button.get("mode", "published")).lower() not in {"draft", "published"}:
                return TemplateValidationError("FLOW mode must be draft or published.")
        elif button_type not in {"QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE", "OTP",
                                 "CATALOG", "MPM", "VOICE_CALL", "APP"}:
            return TemplateValidationError(f"Unsupported button type {button_type!r}.")
    if (counts.get("URL", 0) > 2 or counts.get("PHONE_NUMBER", 0) > 1 or counts.get("COPY_CODE", 0) > 1
            or counts.get("FLOW", 0) > 1 or counts.get("CATALOG", 0) > 1 or counts.get("MPM", 0) > 1
            or counts.get("VOICE_CALL", 0) > 1):
        return TemplateValidationError("Button type count exceeds Meta's template limits.")
    transitions = sum(left != right for left, right in zip(groups, groups[1:]))
    if transitions > 1:
        return TemplateValidationError("Quick-reply and call-to-action buttons cannot be interleaved.")
    return None


def _validate_limited_time_offer(component: dict[str, Any]) -> TemplateValidationError | None:
    text = component.get("limited_time_offer", {}).get("text") if isinstance(
        component.get("limited_time_offer"), dict) else None
    if not isinstance(text, str) or not text:
        return TemplateValidationError("LIMITED_TIME_OFFER requires limited_time_offer.text.")
    if len(text) > 16:
        return TemplateValidationError("LIMITED_TIME_OFFER text cannot exceed 16 characters.")
    return None


def _validate_carousel(component: dict[str, Any], parameter_format: str) -> TemplateValidationError | None:
    cards = component.get("cards")
    if not isinstance(cards, list) or not 2 <= len(cards) <= 10:
        return TemplateValidationError("CAROUSEL must contain between 2 and 10 cards.")
    signatures: list[tuple[Any, ...]] = []
    for card in cards:
        if not isinstance(card, dict):
            return TemplateValidationError("Every carousel card must be an object.")
        card_components = card.get("components")
        if not isinstance(card_components, list) or not card_components:
            return TemplateValidationError("Every carousel card requires a non-empty components array.")
        grouped: dict[str, list[dict[str, Any]]] = {}
        for card_component in card_components:
            if not isinstance(card_component, dict):
                return TemplateValidationError("Every carousel card component must be an object.")
            card_type = str(card_component.get("type", "")).upper()
            if card_type not in {"HEADER", "BODY", "BUTTONS"}:
                return TemplateValidationError(
                    f"Carousel cards cannot contain a {card_type!r} component."
                )
            grouped.setdefault(card_type, []).append(card_component)
        if len(grouped.get("HEADER", [])) != 1:
            return TemplateValidationError("Every carousel card requires exactly one HEADER component.")
        header_format = str(grouped["HEADER"][0].get("format", "")).upper()
        if header_format not in {"IMAGE", "VIDEO"}:
            return TemplateValidationError("Carousel card HEADER format must be IMAGE or VIDEO.")
        if error := _validate_header(grouped["HEADER"][0], parameter_format):
            return error
        if len(grouped.get("BODY", [])) > 1 or len(grouped.get("BUTTONS", [])) > 1:
            return TemplateValidationError("A carousel card can contain at most one BODY and one BUTTONS component.")
        if grouped.get("BODY") and (error := _validate_body(grouped["BODY"][0], parameter_format)):
            return error
        button_types: tuple[str, ...] = ()
        if grouped.get("BUTTONS"):
            buttons = grouped["BUTTONS"][0].get("buttons")
            if not isinstance(buttons, list) or not 1 <= len(buttons) <= 2:
                return TemplateValidationError("A carousel card supports between 1 and 2 buttons.")
            if error := _validate_buttons(grouped["BUTTONS"][0]):
                return error
            button_types = tuple(str(b.get("type", "")).upper() for b in buttons)
        signatures.append((header_format, "BODY" in grouped, button_types))
    if len(set(signatures)) != 1:
        return TemplateValidationError(
            "All carousel cards must have the same components in the same order."
        )
    return None


def _validate_authentication(grouped: dict[str, list[dict[str, Any]]]) -> TemplateValidationError | None:
    body = grouped["BODY"][0]
    if "text" in body:
        return TemplateValidationError("AUTHENTICATION BODY uses Meta-controlled text and cannot include text.")
    buttons = (grouped.get("BUTTONS") or [{}])[0].get("buttons")
    if not isinstance(buttons, list) or len(buttons) != 1 or str(buttons[0].get("type", "")).upper() != "OTP":
        return TemplateValidationError("AUTHENTICATION templates require exactly one OTP button.")
    otp_type = str(buttons[0].get("otp_type", "")).upper()
    if otp_type not in {"COPY_CODE", "ONE_TAP", "ZERO_TAP"}:
        return TemplateValidationError("OTP button otp_type is invalid.")
    if otp_type in {"ONE_TAP", "ZERO_TAP"} and not all(buttons[0].get(key) for key in ("package_name", "signature_hash")):
        return TemplateValidationError(f"{otp_type} requires package_name and signature_hash.")
    return None
