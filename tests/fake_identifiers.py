"""Seeded fake identifiers embedded in discharge-instruction-style text.

All values are invented: SSNs use the never-issued 9xx area, phones use 555-01xx,
emails use example.com. Same seed -> same cases, so detection rates are reproducible.
"""

import random
from dataclasses import dataclass

SEED = 20261013

FIRST = [
    "Maria",
    "Jonah",
    "Priya",
    "Dmitri",
    "Aisha",
    "Tomas",
    "Keiko",
    "Marcus",
    "Elena",
    "Samuel",
    "Fatima",
    "Oliver",
    "Grace",
    "Rafael",
    "Hannah",
    "Kwame",
    "Lucia",
    "Ethan",
]
LAST = [
    "Okafor",
    "Whitfield",
    "Raman",
    "Volkov",
    "Haddad",
    "Lindqvist",
    "Tanaka",
    "Brennan",
    "Moreau",
    "Castillo",
    "Nakamura",
    "Abernathy",
    "Kowalski",
    "Osei",
    "Fitzgerald",
    "Duarte",
]
STREETS = [
    "Birch Lane",
    "Old Mill Road",
    "Harbor View Drive",
    "Maple Street",
    "Elm Court",
    "Prospect Avenue",
    "Willow Way",
]
CITIES = [
    ("Springfield", "MA", "01103"),
    ("Lowell", "MA", "01850"),
    ("Worcester", "MA", "01608"),
    ("Providence", "RI", "02903"),
    ("Nashua", "NH", "03060"),
]
MEDS = [
    "metoprolol succinate 50 MG",
    "aspirin 81 MG",
    "atorvastatin 10 MG",
    "furosemide 40 MG",
    "lisinopril 10 MG",
    "apixaban 5 MG",
]


@dataclass(frozen=True)
class FakeCase:
    text: str
    identifiers: dict[str, str]  # kind -> value embedded in text
    clinical: tuple[str, ...]  # clinical phrases that must survive redaction


def _phone(rng: random.Random) -> str:
    n = f"{rng.randint(200, 989)}555{rng.randint(100, 199):04d}"
    fmt = rng.choice(["({}) {}-{}", "{}-{}-{}", "{}.{}.{}"])
    return fmt.format(n[:3], n[3:6], n[6:])


def _date(rng: random.Random) -> str:
    y, m, d = rng.randint(2024, 2026), rng.randint(1, 12), rng.randint(1, 28)
    months = [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ]
    return rng.choice([f"{m:02d}/{d:02d}/{y}", f"{y}-{m:02d}-{d:02d}", f"{months[m - 1]} {d}, {y}"])


def make_case(rng: random.Random) -> FakeCase:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    doctor = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    city, state, zip_code = rng.choice(CITIES)
    street = f"{rng.randint(1, 9999)} {rng.choice(STREETS)}"
    ids = {
        "name": f"{first} {last}",
        "doctor": doctor,
        "phone": _phone(rng),
        "email": f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@example.com",
        "mrn": f"MRN-{rng.randint(10_000_000, 99_999_999)}",
        "ssn": f"9{rng.randint(10, 99)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}",
        "date": _date(rng),
        "street": street,
        "state_zip": f"{state} {zip_code}",
    }
    med = rng.choice(MEDS)
    days = rng.choice([7, 14, 30])
    templates = [
        f"{ids['name']} ({ids['mrn']}) was discharged home. Follow up with Dr. {doctor} "
        f"on {ids['date']}. Take {med} daily for {days} days.",
        f"Call {ids['phone']} with questions. Patient lives at {ids['street']}, {city} "
        f"{ids['state_zip']}. Take {med} daily for {days} days.",
        f"Send records to {ids['email']}. SSN on file {ids['ssn']}. Take {med} daily for "
        f"{days} days. Contact Dr. {doctor} if short of breath.",
    ]
    text = rng.choice(templates)
    present = {k: v for k, v in ids.items() if v in text}
    return FakeCase(text=text, identifiers=present, clinical=(med, f"for {days} days"))


def fake_cases(n: int = 60, seed: int = SEED) -> list[FakeCase]:
    rng = random.Random(seed)
    return [make_case(rng) for _ in range(n)]
