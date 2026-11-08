"""One way to write a phone number, so one person is one contact (LIVESTACK-51 item 2).

The bug, on the 2026-11-07 test: the admin typed a filmmaker's number as
people write it today, "+55 19 9xxxx-xxxx", with the mobile 9th digit, and
gave it a default prova. WhatsApp identifies that (older) number *without*
the 9th digit, "5519xxxxxxxx@s.whatsapp.net", so intake recorded the
videos under a second contact, "unknown" and without a default prova, and
none of them got the prova. Another contact had been typed without the
country code ("19 9xxxx-xxxx"), which no JID ever matches.

The rule, used everywhere a phone is matched (intake, the admin's
Contatos, the queue's "Enviado por", duplicates' names):

* Brazilian mobile = 55 + DDD (2 digits) + 9 + 8 digits. A Brazilian
  number of 12 digits whose 8-digit part starts with 6, 7, 8 or 9 is a
  mobile written without the 9th digit: the 9 is added. A landline (8-digit
  part starting with 2 to 5) is left alone.
* A number *typed* with 10 or 11 digits that looks like DDD + number
  (DDD without a 0; 11 digits = a mobile starting with 9, 10 digits = 8
  digits starting with 2 to 9) is Brazilian without the country code: 55
  is added. JIDs always carry the country code, so this is only for typing.
* Anything else (other countries, odd lengths) is kept as its digits.

So "+55 19 98123-4567", "19 98123-4567" and the JID "551981234567" are all
"5519981234567": the form the admin types today.
"""

import re

BRAZIL = "55"
MIN_PHONE_DIGITS = 8
MAX_PHONE_DIGITS = 20


def with_ninth_digit(digits: str) -> str:
    """A Brazilian mobile written without the 9th digit gets it; anything else as it is."""
    is_brazilian_12 = len(digits) == 12 and digits.startswith(BRAZIL)
    if is_brazilian_12 and digits[4] in "6789":
        return digits[:4] + "9" + digits[4:]
    return digits


def with_country_code(digits: str) -> str:
    """A Brazilian number typed without +55 ("19 98123-4567") gets it; anything else as it is."""
    has_ddd = len(digits) in (10, 11) and digits[0] != "0" and digits[1] != "0"
    if has_ddd and len(digits) == 11 and digits[2] == "9":
        return BRAZIL + digits
    if has_ddd and len(digits) == 10 and digits[2] in "23456789":
        return BRAZIL + digits
    return digits


def canonical_phone_from_jid_digits(digits: str) -> str:
    """The contact key of a WhatsApp JID's number (which always has its country code)."""
    return with_ninth_digit(digits)


def canonical_typed_phone(typed: str) -> str | None:
    """The contact key of a number an admin typed ("+55 19 98123-4567"); None if implausible."""
    digits = re.sub(r"\D", "", typed)
    if not MIN_PHONE_DIGITS <= len(digits) <= MAX_PHONE_DIGITS:
        return None
    return with_ninth_digit(with_country_code(digits))
