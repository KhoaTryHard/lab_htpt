"""Server validation matching shared/validation.mjs, including JS string lengths."""

import re
from typing import Any, TypedDict

# ECMAScript whitespace differs from Python's str.strip()/\s for a few code points.
JS_WHITESPACE = (
    "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
EMAIL_PATTERN = re.compile(rf"[^{JS_WHITESPACE}@]+@[^{JS_WHITESPACE}@]+\.[^{JS_WHITESPACE}@]+")


class ValidationResult(TypedDict):
    value: dict[str, str]
    errors: dict[str, str]
    valid: bool


def js_length(value: str) -> int:
    """JavaScript String.length counts UTF-16 code units, including surrogate pairs."""
    return len(value.encode("utf-16-le", errors="surrogatepass")) // 2


def validate_registration(data: Any = None) -> ValidationResult:
    source = data if isinstance(data, dict) else {}
    value = {
        key: source[key] if isinstance(source.get(key), str) else ""
        for key in ("fullName", "email", "password", "confirmPassword")
    }
    value["fullName"] = value["fullName"].strip(JS_WHITESPACE)
    value["email"] = value["email"].strip(JS_WHITESPACE).lower()
    errors: dict[str, str] = {}

    if not value["fullName"]:
        errors["fullName"] = "Họ tên là bắt buộc."
    elif not 2 <= js_length(value["fullName"]) <= 80:
        errors["fullName"] = "Họ tên phải dài từ 2 đến 80 ký tự."

    if not value["email"]:
        errors["email"] = "Email là bắt buộc."
    elif js_length(value["email"]) > 254 or not EMAIL_PATTERN.fullmatch(value["email"]):
        errors["email"] = "Email không đúng định dạng."

    if not value["password"]:
        errors["password"] = "Mật khẩu là bắt buộc."
    elif not 8 <= js_length(value["password"]) <= 64:
        errors["password"] = "Mật khẩu phải dài từ 8 đến 64 ký tự."

    if not value["confirmPassword"]:
        errors["confirmPassword"] = "Vui lòng xác nhận mật khẩu."
    elif value["confirmPassword"] != value["password"]:
        errors["confirmPassword"] = "Mật khẩu xác nhận không khớp."

    return {"value": value, "errors": errors, "valid": not errors}
