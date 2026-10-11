"""One portability policy for published ZIPs and provider member locators."""

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def portable_archive_name(name: str) -> bool:
    return all(
        part not in {"", ".", ".."}
        and not any(char in '\\:*?"<>|' or ord(char) < 32 for char in part)
        and not part.endswith((" ", "."))
        and part.split(".", 1)[0].upper() not in WINDOWS_RESERVED_NAMES
        and len(part.encode("utf-8")) <= 255
        for part in name.split("/")
    )
