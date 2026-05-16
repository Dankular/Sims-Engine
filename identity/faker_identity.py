import random

try:
    from faker import Faker as _Faker

    _faker = _Faker()
    FAKER_OK = True
except ImportError:
    _faker = None
    FAKER_OK = False


def generate_identity() -> dict:
    if FAKER_OK:
        rng = random.Random()
        return {
            "first_name": _faker.first_name(),
            "last_name": _faker.last_name(),
            "age": rng.randint(22, 40),
            "email": _faker.email(),
            "username": _faker.user_name(),
            "city": _faker.city(),
            "country": _faker.country(),
        }
    rng = random.Random()
    first = rng.choice(["Alex", "Jordan", "Sam", "Casey", "Riley", "Morgan"])
    last = rng.choice(["Smith", "Chen", "Garcia", "Patel", "Kim", "Santos"])
    age = rng.randint(22, 40)
    return {
        "first_name": first,
        "last_name": last,
        "age": age,
        "email": f"{first.lower()}.{last.lower()}@example.com",
        "username": f"{first.lower()}{rng.randint(10, 99)}",
        "city": "Unknown",
        "country": "Unknown",
    }
