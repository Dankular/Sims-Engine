import random
import uuid

from config import (
    AGE_TRAIT_CANDIDATES,
    ASPIRATIONS,
    DEALBREAKERS_POOL,
    DIETS,
    INTERESTS_POOL,
    JOBS,
    LIKES_POOL,
    DISLIKES_POOL,
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
    if age <= 2:
        traits.append(
            rng.choice(AGE_TRAIT_CANDIDATES.get("infant", ["calm_temperament"]))
        )
    elif age <= 4:
        traits.append(rng.choice(AGE_TRAIT_CANDIDATES.get("toddler", ["inquisitive"])))
    elif age <= 12:
        traits.append(rng.choice(AGE_TRAIT_CANDIDATES.get("child", ["explorer_past"])))
    elif age <= 17:
        traits.append(rng.choice(AGE_TRAIT_CANDIDATES.get("teen", ["competitive"])))
    traits = list(dict.fromkeys(traits))
    dealbreakers = rng.sample(DEALBREAKERS_POOL, k=rng.randint(1, 3))
    aspiration = rng.choice(ASPIRATIONS)
    likes = rng.sample(LIKES_POOL, k=rng.randint(2, 4))
    dislikes = rng.sample(DISLIKES_POOL, k=rng.randint(2, 4))
    personality5 = {
        "neat": round(rng.uniform(0, 10), 2),
        "outgoing": round(rng.uniform(0, 10), 2),
        "active": round(rng.uniform(0, 10), 2),
        "playful": round(rng.uniform(0, 10), 2),
        "nice": round(rng.uniform(0, 10), 2),
    }
    turn_ons = rng.sample(traits, k=min(2, len(traits)))
    turn_off = rng.choice([t for t in TRAITS_POOL if t not in turn_ons])

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
    from datasets.culture import sample_cultural_background

    mbti = get_mbti(ocean, self_summary)
    cultural_background = sample_cultural_background()

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
        "likes": likes,
        "dislikes": dislikes,
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
        "cultural_background": cultural_background,
        "parent_ids": list(parent_ids) if parent_ids else [],
        "attraction_profile": {
            "turn_ons": turn_ons,
            "turn_off": turn_off,
            "aspiration": aspiration,
            "zodiac": "",
            "personality": personality5,
            "gender_preference": {
                "male": round(rng.uniform(0, 1), 2),
                "female": round(rng.uniform(0, 1), 2),
            },
        },
        "genes": {
            "eye_gene": (
                rng.choice(["brown", "dark_blue", "green", "light_blue", "gray"]),
                rng.choice(["brown", "dark_blue", "green", "light_blue", "gray"]),
            ),
            "hair_gene": (
                rng.choice(["black", "brown", "blond", "red", "gray"]),
                rng.choice(["black", "brown", "blond", "red", "gray"]),
            ),
            "skin_gene": (
                round(rng.uniform(0.0, 1.0), 2),
                round(rng.uniform(0.0, 1.0), 2),
            ),
        },
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
    last_name = rng.choice(
        [
            parent_a["name"].split()[-1],
            parent_b["name"].split()[-1],
        ]
    )
    first_name = identity.get("first_name", "Sim")
    name = f"{first_name} {last_name}"
    age = rng.randint(18, 22)

    # OCEAN — blend both parents with noise as the base
    ocean_keys = [
        "openness",
        "conscientiousness",
        "extraversion",
        "agreeableness",
        "neuroticism",
    ]
    blended_ocean = {
        k: round(
            max(
                0.0,
                min(
                    1.0,
                    (parent_a["ocean"][k] + parent_b["ocean"][k]) / 2
                    + rng.uniform(-0.08, 0.08),
                ),
            ),
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
        extras = [t for t in TRAITS_POOL if t not in inherited]
        inherited.append(rng.choice(extras))

    # Other fields
    interests = rng.sample(INTERESTS_POOL, k=rng.randint(3, 4))
    dealbreakers = rng.sample(DEALBREAKERS_POOL, k=rng.randint(1, 2))
    aspiration = rng.choice(ASPIRATIONS)
    diet = rng.choice(DIETS)
    job = rng.choice(JOBS)

    humor_types = [
        "dry",
        "slapstick",
        "dark",
        "self-deprecating",
        "absurdist",
        "wholesome",
    ]
    humor = humor_types[int(ocean["openness"] * (len(humor_types) - 1))]
    comm_style = (
        "enthusiastic and verbose"
        if ocean["extraversion"] > 0.7
        else "warm and measured"
        if ocean["extraversion"] > 0.4
        else "reserved, opens up slowly"
    )
    n, a = ocean["neuroticism"], ocean["agreeableness"]
    attachment = (
        "secure"
        if n < 0.4 and a > 0.5
        else "anxious"
        if n > 0.6
        else "avoidant"
        if a < 0.35
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
    from datasets.culture import sample_cultural_background
    from core.genetics import pick_gene_pair, inherit_skin_tone, inherit_hidden_traits

    mbti = get_mbti(ocean, self_summary)
    # Child inherits one parent's cultural background
    cultural_background = (
        rng.choice(
            [
                parent_a.get("cultural_background", ""),
                parent_b.get("cultural_background", ""),
            ]
        )
        or sample_cultural_background()
    )

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
        "cultural_background": cultural_background,
        "parent_ids": parent_ids,
        "attraction_profile": {
            "turn_ons": rng.sample(inherited, k=min(2, len(inherited))),
            "turn_off": rng.choice([t for t in TRAITS_POOL if t not in inherited])
            if inherited
            else rng.choice(TRAITS_POOL),
            "aspiration": aspiration,
            "zodiac": "",
            "personality": {
                "neat": round(
                    (
                        parent_a.get("attraction_profile", {})
                        .get("personality", {})
                        .get("neat", 5.0)
                        + parent_b.get("attraction_profile", {})
                        .get("personality", {})
                        .get("neat", 5.0)
                    )
                    / 2
                    + rng.uniform(-1.0, 1.0),
                    2,
                ),
                "outgoing": round(
                    (
                        parent_a.get("attraction_profile", {})
                        .get("personality", {})
                        .get("outgoing", 5.0)
                        + parent_b.get("attraction_profile", {})
                        .get("personality", {})
                        .get("outgoing", 5.0)
                    )
                    / 2
                    + rng.uniform(-1.0, 1.0),
                    2,
                ),
                "active": round(
                    (
                        parent_a.get("attraction_profile", {})
                        .get("personality", {})
                        .get("active", 5.0)
                        + parent_b.get("attraction_profile", {})
                        .get("personality", {})
                        .get("active", 5.0)
                    )
                    / 2
                    + rng.uniform(-1.0, 1.0),
                    2,
                ),
                "playful": round(
                    (
                        parent_a.get("attraction_profile", {})
                        .get("personality", {})
                        .get("playful", 5.0)
                        + parent_b.get("attraction_profile", {})
                        .get("personality", {})
                        .get("playful", 5.0)
                    )
                    / 2
                    + rng.uniform(-1.0, 1.0),
                    2,
                ),
                "nice": round(
                    (
                        parent_a.get("attraction_profile", {})
                        .get("personality", {})
                        .get("nice", 5.0)
                        + parent_b.get("attraction_profile", {})
                        .get("personality", {})
                        .get("nice", 5.0)
                    )
                    / 2
                    + rng.uniform(-1.0, 1.0),
                    2,
                ),
            },
            "gender_preference": {
                "male": round(rng.uniform(0, 1), 2),
                "female": round(rng.uniform(0, 1), 2),
            },
        },
        "genes": {
            "eye_gene": pick_gene_pair(
                tuple(parent_a.get("genes", {}).get("eye_gene", ("brown", "green"))),
                tuple(parent_b.get("genes", {}).get("eye_gene", ("brown", "green"))),
            ),
            "hair_gene": pick_gene_pair(
                tuple(parent_a.get("genes", {}).get("hair_gene", ("brown", "black"))),
                tuple(parent_b.get("genes", {}).get("hair_gene", ("brown", "black"))),
            ),
            "skin_gene": (
                inherit_skin_tone(
                    tuple(parent_a.get("genes", {}).get("skin_gene", (0.2, 0.8))),
                    tuple(parent_b.get("genes", {}).get("skin_gene", (0.2, 0.8))),
                ),
                inherit_skin_tone(
                    tuple(parent_a.get("genes", {}).get("skin_gene", (0.2, 0.8))),
                    tuple(parent_b.get("genes", {}).get("skin_gene", (0.2, 0.8))),
                ),
            ),
        },
    }
    profile.update(inherit_hidden_traits(parent_a, parent_b))
    return enrich_profile_with_zodiac(profile)
