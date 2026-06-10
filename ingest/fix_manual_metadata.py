"""One-off script to update court_name and url on existing MANUAL chunks.

Qdrant set_payload updates payload in-place without touching vectors.
Run once after bulk ingestion to fix auto-guessed metadata.
"""

from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny

QDRANT_URL = "http://localhost:6333"
COLLECTION  = "nztt_moj"

# case_id -> (court_name, canonical_url)
# url=None means leave existing value (already correct, e.g. web-scraped pages)
METADATA: dict[str, tuple[str, str | None]] = {

    # ── NZ Legislation ────────────────────────────────────────────────────────
    "MANUAL/housing-improvement-regulations-1947": (
        "New Zealand Legislation",
        "https://www.legislation.govt.nz/regulation/public/1947/0200/latest/whole.html",
    ),
    "MANUAL/residential-tenancies-healthy-homes-standards-regulations-2019": (
        "New Zealand Legislation",
        "https://www.legislation.govt.nz/regulation/public/2019/0088/latest/LMS169006.html",
    ),
    "MANUAL/residential-tenancies-managing-methamphetamine-contamination-regulations-2026": (
        "New Zealand Legislation",
        "https://www.legislation.govt.nz/regulation/public/2026/0025/latest/whole.html",
    ),
    "MANUAL/residential-tenancies-amendment-act-2020": (
        "New Zealand Legislation",
        "https://www.legislation.govt.nz/act/public/2020/0059/latest/LMS245585.html",
    ),

    # ── Official Policy / Government ──────────────────────────────────────────
    "MANUAL/privacy-act-guidance-for-landlords": (
        "Office of the Privacy Commissioner",
        "https://www.privacy.org.nz/resources-and-learning/a-z-topics/rental-guidance-for-landlords/",
    ),
    "MANUAL/regulatory-impact-statement-rta-tenancy-termination-amendments": (
        "Ministry of Business Innovation and Employment",
        "https://www.mbie.govt.nz/assets/ris-residential-tenancies-act-tenancy-termination-amendments.pdf",
    ),
    "MANUAL/ris-heating-and-insulation-standards-for-residential-rental-properties-hhg-act-2": (
        "Ministry of Business Innovation and Employment",
        "https://www.mbie.govt.nz/assets/Uploads/heating-and-insulation-standards-ris.pdf",
    ),
    "MANUAL/healthy-homes-standards-discussion-document-september-2018": (
        "Ministry of Housing and Urban Development",
        "https://www.hud.govt.nz/our-work/healthy-homes-standards/",
    ),
    "MANUAL/healthy-homes-guarantee-act-monitoring-wave-4-topline-report-march-2024": (
        "Ministry of Housing and Urban Development",
        "https://www.hud.govt.nz/our-work/healthy-homes-standards/",
    ),

    # ── Advocacy / Community Legal ────────────────────────────────────────────
    "MANUAL/cab-nz-submission-on-residential-tenancies-amendment-bill": (
        "Citizens Advice Bureau New Zealand",
        "https://www.cab.org.nz/",
    ),
    "MANUAL/residential-tenancy-training-for-community-advisers": (
        "New Zealand Law Foundation",
        "https://www.lawfoundation.org.nz/",
    ),

    # ── Law Review / Academic ─────────────────────────────────────────────────
    "MANUAL/compounding-the-abuse-family-violence-damages-and-the-tenancy-tribunal": (
        "New Zealand Universities Law Review",
        "https://www.nzulr.auckland.ac.nz/",
    ),
    "MANUAL/problems-in-residential-tenancy-law-revealed-by-holler-v-osaki": (
        "Otago Law Review",
        "https://www.otago.ac.nz/law/research/journals/otago-law-review.html",
    ),
    "MANUAL/security-of-tenure-for-generation-rent-irish-and-scottish-approaches": (
        "Otago Law Review",
        "https://www.otago.ac.nz/law/research/journals/otago-law-review.html",
    ),
    "MANUAL/renting-in-new-zealand-perspectives-from-tenant-advocates": (
        "Kotuitui: NZ Journal of Social Sciences Online",
        "https://www.tandfonline.com/journals/tnzk20",
    ),
    "MANUAL/getting-the-balance-right": (
        "Victoria University of Wellington",
        "https://openaccess.wgtn.ac.nz/",
    ),
    "MANUAL/the-residential-tenancies-act-1986": (
        "New Zealand Recent Law Review",
        "https://www.lawreview.co.nz/",
    ),
    "MANUAL/the-residential-tenancies-act-1986-a-commentary": (
        "NZ Law Foundation Commentary",
        "https://www.lawfoundation.org.nz/",
    ),

    # ── Tenancy Services pages (web-scraped - keep existing URLs, fix court_name) ──
    "MANUAL/bonds": ("Tenancy Services", None),
    "MANUAL/how-to-apply-for-a-bond-refund": ("Tenancy Services", None),
    "MANUAL/rules-about-pets": ("Tenancy Services", None),
    "MANUAL/requesting-pet-consent": ("Tenancy Services", None),
    "MANUAL/pet-consent-conditions": ("Tenancy Services", None),
    "MANUAL/pet-rules-to-30-november-2025": ("Tenancy Services", None),
    "MANUAL/discrimination": ("Tenancy Services", None),
    "MANUAL/giving-notice-to-end-a-tenancy": ("Tenancy Services", None),
    "MANUAL/ending-a-periodic-tenancy": ("Tenancy Services", None),
    "MANUAL/ending-a-tenancy": ("Tenancy Services", None),
    "MANUAL/tenants-ending-a-tenancy-process": ("Tenancy Services", None),
    "MANUAL/landlords-ending-a-tenancy-process": ("Tenancy Services", None),
    "MANUAL/periodic-or-fixed-term-tenancy": ("Tenancy Services", None),
    "MANUAL/expiry-of-a-fixed-term-tenancy": ("Tenancy Services", None),
    "MANUAL/rent-increases-and-reductions": ("Tenancy Services", None),
    "MANUAL/rent-arrears-and-overdue-rent": ("Tenancy Services", None),
    "MANUAL/rental-guidance-for-landlords-and-property-managers": (
        "Office of the Privacy Commissioner", None
    ),
}


def main() -> None:
    client = QdrantClient(url=QDRANT_URL)

    for case_id, (court_name, url) in METADATA.items():
        filt = Filter(must=[FieldCondition(key="case_id", match=MatchValue(value=case_id))])

        # Count matching points first
        count = client.count(collection_name=COLLECTION, count_filter=filt).count
        if count == 0:
            print(f"  SKIP (0 points): {case_id}")
            continue

        payload: dict = {"court_name": court_name}
        if url is not None:
            payload["url"] = url

        client.set_payload(
            collection_name=COLLECTION,
            payload=payload,
            points=filt,
        )
        updated = f"court_name={court_name}" + (f", url={url[:60]}" if url else "")
        print(f"  [{count:3d} pts] {case_id[:55]:<55} -> {updated[:60]}")

    print("\nDone.")


if __name__ == "__main__":
    main()
