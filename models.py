from dataclasses import dataclass, field

@dataclass
class Result:
    query: str
    company: str | None = None
    country: str | None = None
    products: list[str] = field(default_factory=list)
    website: str | None = None
    pages_scanned: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    priority_emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    whatsapp_links: list[str] = field(default_factory=list)
    whatsapp_numbers: list[str] = field(default_factory=list)
    whatsapp_verified: list[str] = field(default_factory=list)
    social_links: list[str] = field(default_factory=list)
    guessed_emails: list[str] = field(default_factory=list)
    hunter_emails: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    is_direct_hit: bool = False

@dataclass
class ContactExtract:
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    social_links: set[str] = field(default_factory=set)
    whatsapp_links: set[str] = field(default_factory=set)
    whatsapp_numbers: set[str] = field(default_factory=set)
