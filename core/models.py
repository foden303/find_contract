from dataclasses import dataclass, field


@dataclass
class Candidate:
    """A search hit being considered as the company's website."""

    url: str
    title: str = ""
    snippet: str = ""
    query: str = ""
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    is_directory: bool = False
    is_snippet_only: bool = False


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

    # How well `website` matches the company we were asked about (0-100)
    confidence: float = 0.0
    # Human-readable justification for `confidence`
    match_reason: list[str] = field(default_factory=list)
    # Other candidates we considered, best-first: "score url"
    alternates: list[str] = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def has_contact(self) -> bool:
        return bool(self.priority_emails or self.emails or self.phones)


@dataclass
class ContactExtract:
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    social_links: set[str] = field(default_factory=set)
    whatsapp_links: set[str] = field(default_factory=set)
    whatsapp_numbers: set[str] = field(default_factory=set)

    def merge(self, other: "ContactExtract") -> None:
        self.emails |= other.emails
        self.phones |= other.phones
        self.social_links |= other.social_links
        self.whatsapp_links |= other.whatsapp_links
        self.whatsapp_numbers |= other.whatsapp_numbers
