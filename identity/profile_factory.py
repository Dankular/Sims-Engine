import random
import uuid

from config import (
    ASPIRATIONS,
    DEALBREAKERS_POOL,
    DIETS,
    INTERESTS_POOL,
    JOBS,
    TRAITS_POOL,
)
from identity.faker_identity import generate_identity
from identity.ocean_scorer import ocean_from_text


def generate_sim_profile(
    sim_id: str | None = None,
    okcupid_essays: list[str] | None = None,
    parent_ids: list[str] | None = None,
) -> dict:
    rng = random.Random()
    sim_id = sim_id or str(uuid.uuid4())
    identity = generate_identity()

    name = (
        f"{identity.get('first_name', 'Sim')} {identity.get('last_name', '')}".strip()
    )
    age = int(identity.get("age", rng.randint(22, 40)))
    gender = rng.choice(["man", "woman", "non-binary", "woman", "man"])
    diet = rng.choice(DIETS)
    job = rng.choice(JOBS)
    income = rng.choice(["low", "medium", "high"])
    interests = rng.sample(INTERESTS_POOL, k=rng.randint(3, 5))
    traits = rng.sample(TRAITS_POOL, k=rng.randint(2, 4))
    dealbreakers = rng.sample(DEALBREAKERS_POOL, k=rng.randint(1, 3))
    aspiration = rng.choice(ASPIRATIONS)

    # Use a real OkCupid essay for OCEAN scoring when available
    essay = random.choice(okcupid_essays) if okcupid_essays else None
    ocean = ocean_from_text(essay or identity.get("username", sim_id))
    ocean_source = "personality_lm" if essay else "synthetic"

    humor_types = [
        "dry",
        "slapstick",
        "dark",
        "self-deprecating",
        "absurdist",
        "wholesome",
    ]
    humor = humor_types[int(ocean["openness"] * (len(humor_types) - 1))]
    if ocean["extraversion"] > 0.7:
        comm_style = "enthusiastic and verbose"
    elif ocean["extraversion"] > 0.4:
        comm_style = "warm and measured"
    else:
        comm_style = "reserved, opens up slowly"

    neuroticism, agreeableness = ocean["neuroticism"], ocean["agreeableness"]
    if neuroticism < 0.4 and agreeableness > 0.5:
        attachment = "secure"
    elif neuroticism > 0.6:
        attachment = "anxious"
    elif agreeableness < 0.35:
        attachment = "avoidant"
    else:
        attachment = "secure-leaning"

    self_summary = (
        f"I work as a {job} and genuinely love it. "
        f"Huge into {interests[0]} and {interests[1]}. "
        f"I'm {traits[0]} and probably {traits[1]}. "
        f"Important to me: {aspiration.lower()} above all. "
        f"Can't stand {dealbreakers[0]}."
    )

    from identity.mbti import get_mbti, mbti_descriptor
    from identity.zodiac import enrich_profile_with_zodiac
    mbti = get_mbti(ocean, self_summary)

    profile = {
        "id": sim_id,
        "name": name,
        "age": age,
        "email": identity.get("email", ""),
        "city": identity.get("city", "Unknown"),
        "country": identity.get("country", "Unknown"),
        "identity_source": "faker"
        if identity.get("city", "Unknown") != "Unknown"
        else "fallback",
        "ocean_source": ocean_source,
        "gender": gender,
        "diet": diet,
        "job": job,
        "income": income,
        "interests": interests,
        "traits": traits,
        "dealbreakers": dealbreakers,
        "aspiration": aspiration,
        "ocean": ocean,
        "humor_type": humor,
        "comm_style": comm_style,
        "attachment": attachment,
        "self_summary": self_summary,
        "mbti": mbti,
        "mbti_descriptor": mbti_descriptor(mbti),
        "parent_ids": list(parent_ids) if parent_ids else [],
    }
    return enrich_profile_with_zodiac(profile)


def generate_child_profile(
    parent_a: dict,
    parent_b: dict,
    okcupid_essays: list[str] | None = None,
) -> dict:
    """Generate a profile for a child born to two sims.

    OCEAN is a blended average of both parents with small random variation.
    Traits are inherited: 1-2 random picks from each parent's pool.
    The child enters the world as a young adult (age 18-22).
    """
    rng = random.Random()
    sim_id = str(uuid.uuid4())
    parent_ids = [parent_a["id"], parent_b["id"]]

    # Identity — child takes one parent's last name
    from identity.faker_identity import generate_identity
    identity = generate_identity()
    last_name = rng.choice([
        parent_a["name"].split()[-1],
        parent_b["name"].split()[-1],
    ])
    first_name = identity.get("first_name", "Sim")
    name = f"{first_name} {last_name}"
    age = rng.randint(18, 22)

    # OCEAN — blend both parents with noise as the base
    ocean_keys = ["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]
    blended_ocean = {
        k: round(
            max(0.0, min(1.0,
                (parent_a["ocean"][k] + parent_b["ocean"][k]) / 2
                + rng.uniform(-0.08, 0.08)
            )),
            2,
        )
        for k in ocean_keys
    }
    ocean = blended_ocean
    ocean_source = "inherited_blend"

    # Traits — inherit from each parent
    a_traits = parent_a.get("traits", [])
    b_traits = parent_b.get("traits", [])
    inherited: list[str] = []
    if a_traits:
        inherited.append(rng.choice(a_traits))
    if b_traits:
        pick = rng.choice(b_traits)
        if pick not in inherited:
            inherited.append(pick)
    if len(inherited) < 2:
        from config import TRAITS_POOL
        extras = [t for t in TRAITS_POOL if t not in inherited]
        inherited.append(rng.choice(extras))

    # Other fields
    from config import INTERESTS_POOL, DEALBREAKERS_POOL, ASPIRATIONS, DIETS, JOBS
    interests = rng.sample(INTERESTS_POOL, k=rng.randint(3, 4))
    dealbreakers = rng.sample(DEALBREAKERS_POOL, k=rng.randint(1, 2))
    aspiration = rng.choice(ASPIRATIONS)
    diet = rng.choice(DIETS)
    job = rng.choice(JOBS)

    humor_types = ["dry", "slapstick", "dark", "self-deprecating", "absurdist", "wholesome"]
    humor = humor_types[int(ocean["openness"] * (len(humor_types) - 1))]
    comm_style = (
        "enthusiastic and verbose" if ocean["extraversion"] > 0.7
        else "warm and measured" if ocean["extraversion"] > 0.4
        else "reserved, opens up slowly"
    )
    n, a = ocean["neuroticism"], ocean["agreeableness"]
    attachment = (
        "secure" if n < 0.4 and a > 0.5
        else "anxious" if n > 0.6
        else "avoidant" if a < 0.35
        else "secure-leaning"
    )

    parent_a_name = parent_a["name"].split()[0]
    parent_b_name = parent_b["name"].split()[0]
    self_summary = (
        f"I'm the child of {parent_a_name} and {parent_b_name}. "
        f"I work as a {job}. Huge into {interests[0]} and {interests[1]}. "
        f"I'm {inherited[0]} — just like my parent. "
        f"Important to me: {aspiration.lower()} above all."
    )

    # Try short-text OCEAN scorer on self_summary — child has no essay
    from identity.ocean_scorer import ocean_from_short_text
    inferred_ocean = ocean_from_short_text(self_summary)
    if inferred_ocean:
        ocean = inferred_ocean
        ocean_source = "personality_lm_short"

    from identity.mbti import get_mbti, mbti_descriptor
    from identity.zodiac import enrich_profile_with_zodiac
    mbti = get_mbti(ocean, self_summary)

    profile = {
        "id": sim_id,
        "name": name,
        "age": age,
        "email": identity.get("email", ""),
        "city": parent_a.get("city", "Unknown"),
        "country": parent_a.get("country", "Unknown"),
        "identity_source": "child",
        "ocean_source": ocean_source,
        "gender": rng.choice(["man", "woman", "non-binary", "woman", "man"]),
        "diet": diet,
        "job": job,
        "income": "low",
        "interests": interests,
        "traits": inherited,
        "dealbreakers": dealbreakers,
        "aspiration": aspiration,
        "ocean": ocean,
        "humor_type": humor,
        "comm_style": comm_style,
        "attachment": attachment,
        "self_summary": self_summary,
        "mbti": mbti,
        "mbti_descriptor": mbti_descriptor(mbti),
        "parent_ids": parent_ids,
    }
    return enrich_profile_with_zodiac(profile)
