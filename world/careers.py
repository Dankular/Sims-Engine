"""
world/careers.py — Career catalogue and progression definitions.

15 career tracks, each with 5–7 levels, branches from level 5, and
promotion requirements based on performance, skills, and reputation.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class PromotionReq:
    performance: float = 60.0
    skills: dict[str, float] = field(default_factory=dict)
    reputation: float = -100.0  # minimum reputation required (-100 = no gate)
    friendship_count: int = 0   # min workplace friends needed
    days_in_role: int = 0       # min days before promotion eligible


@dataclass
class CareerLevel:
    level: int
    title: str
    salary_per_tick: float      # simoleons per pay tick (every 5 ticks)
    promotion_req: PromotionReq
    branch: str = "base"        # "base" or branch id
    description: str = ""


@dataclass
class CareerDef:
    career_id: str
    name: str
    category: str               # "full_time", "part_time", "active", "passive", "freelance"
    description: str
    schedule: str               # "9to5", "night", "flexible", "variable"
    related_skills: list[str]
    levels: list[CareerLevel]   # index 0 = level 1
    branches: dict[str, str]    # branch_id → display name, available from level 5
    entry_req: PromotionReq = field(default_factory=PromotionReq)

    def get_level(self, level: int, branch: str = "base") -> CareerLevel | None:
        matches = [l for l in self.levels if l.level == level and (l.branch == branch or l.branch == "base")]
        return matches[0] if matches else None

    def max_level(self) -> int:
        return max(l.level for l in self.levels)


def _lvl(level, title, salary, perf=60, skills=None, rep=-100, days=0, branch="base", desc="") -> CareerLevel:
    return CareerLevel(
        level=level, title=title, salary_per_tick=salary,
        promotion_req=PromotionReq(performance=perf, skills=skills or {}, reputation=rep, days_in_role=days),
        branch=branch, description=desc,
    )


CAREER_CATALOGUE: dict[str, CareerDef] = {

    "tech": CareerDef(
        career_id="tech", name="Technology", category="full_time",
        description="From IT support to software architect. Skill-heavy career with two specialization paths.",
        schedule="9to5", related_skills=["programming", "logic", "video_gaming"],
        branches={"gamedev": "Game Developer", "backend": "Backend Architect"},
        levels=[
            _lvl(1, "IT Support Technician",   30,  perf=55, skills={"programming": 1}),
            _lvl(2, "Junior Developer",         55,  perf=60, skills={"programming": 2, "logic": 2}),
            _lvl(3, "Software Developer",       85,  perf=65, skills={"programming": 4, "logic": 3}),
            _lvl(4, "Senior Developer",        120,  perf=70, skills={"programming": 6, "logic": 4}),
            _lvl(5, "Lead Engineer",           165,  perf=75, skills={"programming": 7, "logic": 5}),
            _lvl(6, "Game Engine Lead",        210,  perf=80, skills={"programming": 8}, branch="gamedev"),
            _lvl(6, "Principal Architect",     210,  perf=80, skills={"programming": 9}, branch="backend"),
            _lvl(7, "Studio Technical Director",260, perf=85, skills={"programming": 10}, branch="gamedev"),
            _lvl(7, "Chief Technology Officer", 280, perf=88, skills={"programming": 10, "logic": 8}, branch="backend"),
        ],
    ),

    "medicine": CareerDef(
        career_id="medicine", name="Medicine", category="full_time",
        description="From intern to chief of surgery or research director. Demands high skills and reputation.",
        schedule="variable", related_skills=["wellness", "logic", "charisma"],
        branches={"surgery": "Surgeon", "research": "Medical Researcher"},
        entry_req=PromotionReq(reputation=-20.0),
        levels=[
            _lvl(1, "Medical Intern",          35,  perf=55, skills={"wellness": 1, "logic": 2}),
            _lvl(2, "Resident Physician",      65,  perf=60, skills={"wellness": 3, "logic": 3}),
            _lvl(3, "General Practitioner",   100,  perf=65, skills={"wellness": 5, "logic": 4}),
            _lvl(4, "Specialist",             145,  perf=70, skills={"wellness": 6, "logic": 5}),
            _lvl(5, "Senior Consultant",      195,  perf=75, skills={"wellness": 7, "logic": 6}),
            _lvl(6, "Head of Surgery",        260,  perf=80, skills={"wellness": 9}, branch="surgery"),
            _lvl(6, "Research Director",      240,  perf=78, skills={"logic": 9}, branch="research"),
            _lvl(7, "Chief Surgeon",          320,  perf=88, skills={"wellness": 10}, branch="surgery"),
            _lvl(7, "Chief Research Officer", 300,  perf=85, skills={"logic": 10}, branch="research"),
        ],
    ),

    "law": CareerDef(
        career_id="law", name="Law", category="full_time",
        description="From paralegal to judge or defence attorney. Reputation is everything.",
        schedule="9to5", related_skills=["charisma", "logic", "writing"],
        branches={"corporate": "Corporate Lawyer", "defence": "Criminal Defence Attorney"},
        entry_req=PromotionReq(reputation=-10.0),
        levels=[
            _lvl(1, "Paralegal",              28,  perf=55, skills={"charisma": 1, "logic": 2}),
            _lvl(2, "Junior Associate",        55,  perf=60, skills={"charisma": 3, "logic": 3}),
            _lvl(3, "Associate Attorney",      90,  perf=65, skills={"charisma": 4, "logic": 4}),
            _lvl(4, "Senior Associate",       130,  perf=70, skills={"charisma": 5, "logic": 5}),
            _lvl(5, "Partner",                180,  perf=75, skills={"charisma": 6, "logic": 6, "writing": 4}),
            _lvl(6, "Corporate Partner",      235,  perf=80, skills={"charisma": 8}, branch="corporate"),
            _lvl(6, "Lead Defence Counsel",   220,  perf=78, skills={"charisma": 9}, branch="defence"),
            _lvl(7, "General Counsel",        290,  perf=86, branch="corporate"),
            _lvl(7, "High Court Advocate",    270,  perf=88, branch="defence"),
        ],
    ),

    "business": CareerDef(
        career_id="business", name="Business", category="full_time",
        description="From intern to CEO or marketing director. Networking and charisma drive success.",
        schedule="9to5", related_skills=["charisma", "logic", "writing"],
        branches={"finance": "Finance Executive", "marketing": "Marketing Director"},
        levels=[
            _lvl(1, "Business Intern",        25,  perf=50, skills={"charisma": 1}),
            _lvl(2, "Analyst",                48,  perf=55, skills={"charisma": 2, "logic": 2}),
            _lvl(3, "Manager",                78,  perf=62, skills={"charisma": 3, "logic": 3}),
            _lvl(4, "Senior Manager",        115,  perf=68, skills={"charisma": 4, "logic": 4}),
            _lvl(5, "Director",              158,  perf=73, skills={"charisma": 5, "logic": 5}),
            _lvl(6, "VP of Finance",         205,  perf=78, skills={"logic": 7}, branch="finance"),
            _lvl(6, "VP of Marketing",       195,  perf=76, skills={"charisma": 7}, branch="marketing"),
            _lvl(7, "Chief Financial Officer",265, perf=85, branch="finance"),
            _lvl(7, "Chief Marketing Officer",250, perf=83, branch="marketing"),
        ],
    ),

    "arts": CareerDef(
        career_id="arts", name="Arts & Entertainment", category="passive",
        description="Build a creative portfolio. Income scales with reputation and masterpieces.",
        schedule="flexible", related_skills=["painting", "writing", "guitar", "singing", "dancing"],
        branches={"visual": "Visual Artist", "performing": "Performing Artist"},
        levels=[
            _lvl(1, "Hobbyist",               12,  perf=40, skills={"painting": 1}),
            _lvl(2, "Amateur Artist",          22,  perf=48, skills={"painting": 2}),
            _lvl(3, "Working Artist",          38,  perf=55, skills={"painting": 4}),
            _lvl(4, "Recognized Artist",       60,  perf=62, skills={"painting": 5}, rep=10.0),
            _lvl(5, "Established Artist",      90,  perf=68, skills={"painting": 6}, rep=25.0),
            _lvl(6, "Gallery Artist",         130,  perf=75, skills={"painting": 8}, branch="visual", rep=40.0),
            _lvl(6, "Touring Performer",      125,  perf=73, skills={"guitar": 7}, branch="performing", rep=35.0),
            _lvl(7, "Master Artist",          180,  perf=82, skills={"painting": 10}, branch="visual", rep=60.0),
            _lvl(7, "Celebrity Performer",    200,  perf=85, skills={"singing": 8, "dancing": 5}, branch="performing", rep=70.0),
        ],
    ),

    "sports": CareerDef(
        career_id="sports", name="Sports & Athletics", category="active",
        description="Peak physical performance career. Fitness is everything; career ends at elder stage.",
        schedule="variable", related_skills=["fitness", "wellness", "charisma"],
        branches={"team": "Team Athlete", "individual": "Solo Competitor"},
        levels=[
            _lvl(1, "Amateur Athlete",         20,  perf=55, skills={"fitness": 2}),
            _lvl(2, "Semi-Pro",                45,  perf=60, skills={"fitness": 3}),
            _lvl(3, "Professional Athlete",    90,  perf=65, skills={"fitness": 5}),
            _lvl(4, "Star Athlete",           145,  perf=72, skills={"fitness": 7}),
            _lvl(5, "MVP",                    200,  perf=78, skills={"fitness": 8}),
            _lvl(6, "Team Captain",           255,  perf=82, skills={"fitness": 9, "charisma": 5}, branch="team"),
            _lvl(6, "Champion",               265,  perf=84, skills={"fitness": 10}, branch="individual"),
            _lvl(7, "Hall of Fame Legend",    310,  perf=90, branch="team", rep=60.0),
            _lvl(7, "World Champion",         320,  perf=92, branch="individual", rep=65.0),
        ],
    ),

    "culinary": CareerDef(
        career_id="culinary", name="Culinary Arts", category="active",
        description="From dishwasher to head chef or restaurant owner.",
        schedule="variable", related_skills=["cooking", "gourmet_cooking", "baking", "charisma"],
        branches={"chef": "Head Chef", "restaurateur": "Restaurant Owner"},
        levels=[
            _lvl(1, "Kitchen Hand",            18,  perf=50, skills={"cooking": 1}),
            _lvl(2, "Prep Cook",               32,  perf=55, skills={"cooking": 2}),
            _lvl(3, "Line Cook",               52,  perf=60, skills={"cooking": 4}),
            _lvl(4, "Sous Chef",               80,  perf=65, skills={"cooking": 6, "gourmet_cooking": 3}),
            _lvl(5, "Chef de Cuisine",        118,  perf=72, skills={"cooking": 7, "gourmet_cooking": 5}),
            _lvl(6, "Executive Chef",         160,  perf=78, skills={"cooking": 9, "gourmet_cooking": 7}, branch="chef"),
            _lvl(6, "Restaurant Manager",     155,  perf=75, skills={"cooking": 7, "charisma": 6}, branch="restaurateur"),
            _lvl(7, "Michelin Head Chef",     210,  perf=85, skills={"gourmet_cooking": 10}, branch="chef"),
            _lvl(7, "Restaurant Empire Owner",220,  perf=82, skills={"charisma": 8}, branch="restaurateur"),
        ],
    ),

    "criminal": CareerDef(
        career_id="criminal", name="Criminal Underworld", category="active",
        description="A life outside the law. Reputation with the wrong crowd matters most.",
        schedule="night", related_skills=["mischief", "charisma", "fitness"],
        branches={"thief": "Master Thief", "con_artist": "Con Artist"},
        entry_req=PromotionReq(reputation=5.0),
        levels=[
            _lvl(1, "Street Hustler",          15,  perf=50, skills={"mischief": 1}),
            _lvl(2, "Petty Criminal",          30,  perf=55, skills={"mischief": 2}),
            _lvl(3, "Career Criminal",         55,  perf=60, skills={"mischief": 4}),
            _lvl(4, "Enforcer",                85,  perf=65, skills={"mischief": 5, "fitness": 4}),
            _lvl(5, "Crime Boss",             130,  perf=72, skills={"mischief": 7, "charisma": 4}),
            _lvl(6, "Master Thief",           175,  perf=78, skills={"mischief": 8, "fitness": 6}, branch="thief"),
            _lvl(6, "Con Artist",             170,  perf=76, skills={"mischief": 8, "charisma": 7}, branch="con_artist"),
            _lvl(7, "Legendary Thief",        220,  perf=85, skills={"mischief": 10}, branch="thief"),
            _lvl(7, "Crime Mastermind",       230,  perf=88, branch="con_artist"),
        ],
    ),

    "politics": CareerDef(
        career_id="politics", name="Politics", category="full_time",
        description="Build a political career through networking, speeches, and reputation.",
        schedule="9to5", related_skills=["charisma", "writing", "logic"],
        branches={"local": "Local Government", "national": "National Politics"},
        entry_req=PromotionReq(reputation=0.0, friendship_count=2),
        levels=[
            _lvl(1, "Campaign Volunteer",      15,  perf=50, skills={"charisma": 1}),
            _lvl(2, "Political Aide",          35,  perf=55, skills={"charisma": 3, "writing": 2}),
            _lvl(3, "Councilmember",           65,  perf=62, skills={"charisma": 4, "writing": 3}, rep=5.0),
            _lvl(4, "City Representative",    100,  perf=68, skills={"charisma": 6}, rep=15.0),
            _lvl(5, "Senator",                150,  perf=74, skills={"charisma": 7, "writing": 5}, rep=30.0),
            _lvl(6, "Mayor",                  195,  perf=78, branch="local", rep=45.0),
            _lvl(6, "Member of Parliament",   200,  perf=80, branch="national", rep=50.0),
            _lvl(7, "Governor",               260,  perf=86, branch="local", rep=60.0),
            _lvl(7, "Prime Minister",         300,  perf=90, branch="national", rep=75.0),
        ],
    ),

    "education": CareerDef(
        career_id="education", name="Education", category="full_time",
        description="Inspire students, advance research. Stable career with intellectual rewards.",
        schedule="9to5", related_skills=["logic", "charisma", "writing"],
        branches={"teacher": "School Teacher", "researcher": "Academic Researcher"},
        levels=[
            _lvl(1, "Teaching Assistant",      22,  perf=50, skills={"logic": 1, "charisma": 1}),
            _lvl(2, "Junior Teacher",          40,  perf=55, skills={"logic": 3, "charisma": 2}),
            _lvl(3, "Teacher",                 60,  perf=60, skills={"logic": 4, "charisma": 3}),
            _lvl(4, "Head of Department",      88,  perf=65, skills={"logic": 5, "charisma": 4}),
            _lvl(5, "Principal",              120,  perf=70, skills={"charisma": 6}),
            _lvl(6, "Master Teacher",         155,  perf=76, skills={"charisma": 8}, branch="teacher"),
            _lvl(6, "Associate Professor",    145,  perf=74, skills={"logic": 8, "writing": 5}, branch="researcher"),
            _lvl(7, "Distinguished Educator", 195,  perf=82, branch="teacher"),
            _lvl(7, "Full Professor",         185,  perf=80, branch="researcher"),
        ],
    ),

    "science": CareerDef(
        career_id="science", name="Science & Research", category="passive",
        description="Push the boundaries of knowledge. Publish papers, make breakthroughs.",
        schedule="flexible", related_skills=["logic", "rocket_science", "programming"],
        branches={"biology": "Life Sciences", "physics": "Physical Sciences"},
        levels=[
            _lvl(1, "Lab Technician",          25,  perf=55, skills={"logic": 2}),
            _lvl(2, "Junior Researcher",        48,  perf=60, skills={"logic": 3}),
            _lvl(3, "Research Scientist",       80,  perf=65, skills={"logic": 5}),
            _lvl(4, "Senior Scientist",        115,  perf=70, skills={"logic": 6, "writing": 3}),
            _lvl(5, "Principal Scientist",     155,  perf=75, skills={"logic": 7}),
            _lvl(6, "Lead Biologist",          195,  perf=80, skills={"logic": 8}, branch="biology"),
            _lvl(6, "Theoretical Physicist",   195,  perf=80, skills={"logic": 9, "rocket_science": 4}, branch="physics"),
            _lvl(7, "Chief Scientific Officer",245,  perf=85, branch="biology"),
            _lvl(7, "Nobel Contender",         250,  perf=88, branch="physics"),
        ],
    ),

    "media": CareerDef(
        career_id="media", name="Media & Journalism", category="active",
        description="From blogger to network anchor or entertainment host.",
        schedule="flexible", related_skills=["writing", "charisma", "photography"],
        branches={"journalism": "Journalist", "entertainment": "Entertainment Host"},
        levels=[
            _lvl(1, "Blogger",                 12,  perf=48, skills={"writing": 1}),
            _lvl(2, "Staff Writer",            28,  perf=54, skills={"writing": 2, "charisma": 1}),
            _lvl(3, "Journalist",              50,  perf=60, skills={"writing": 4, "charisma": 2}),
            _lvl(4, "Senior Reporter",         78,  perf=65, skills={"writing": 5, "charisma": 3}),
            _lvl(5, "Editor",                 112,  perf=70, skills={"writing": 6, "charisma": 4}),
            _lvl(6, "Investigative Reporter", 145,  perf=76, skills={"writing": 8}, branch="journalism"),
            _lvl(6, "TV Host",                155,  perf=78, skills={"charisma": 7}, branch="entertainment"),
            _lvl(7, "Network Anchor",         195,  perf=82, branch="journalism", rep=40.0),
            _lvl(7, "Celebrity Host",         210,  perf=84, branch="entertainment", rep=50.0),
        ],
    ),

    "military": CareerDef(
        career_id="military", name="Military", category="full_time",
        description="Serve and climb the ranks. Discipline, fitness, and loyalty matter.",
        schedule="variable", related_skills=["fitness", "logic", "charisma"],
        branches={"combat": "Combat Officer", "intelligence": "Intelligence Analyst"},
        levels=[
            _lvl(1, "Recruit",                 28,  perf=55, skills={"fitness": 2}),
            _lvl(2, "Private",                 42,  perf=60, skills={"fitness": 3}),
            _lvl(3, "Corporal",                62,  perf=65, skills={"fitness": 4, "logic": 2}),
            _lvl(4, "Sergeant",                88,  perf=70, skills={"fitness": 5, "logic": 3}),
            _lvl(5, "Lieutenant",             120,  perf=74, skills={"fitness": 6, "charisma": 3}),
            _lvl(6, "Captain",                158,  perf=78, skills={"fitness": 7}, branch="combat"),
            _lvl(6, "Intelligence Officer",   152,  perf=76, skills={"logic": 7}, branch="intelligence"),
            _lvl(7, "General",                210,  perf=86, branch="combat", rep=40.0),
            _lvl(7, "Director of Intelligence",200, perf=84, branch="intelligence"),
        ],
    ),

    "freelance": CareerDef(
        career_id="freelance", name="Freelance", category="freelance",
        description="Self-directed income from gigs and commissions. No boss, no ceiling.",
        schedule="flexible", related_skills=["writing", "painting", "programming", "photography"],
        branches={},
        levels=[
            _lvl(1, "Freelancer", 0, perf=0),  # income comes from gigs, not salary
        ],
    ),

    "unemployed": CareerDef(
        career_id="unemployed", name="Unemployed", category="unemployed",
        description="Between jobs. Looking for opportunities.",
        schedule="flexible", related_skills=[],
        branches={},
        levels=[
            _lvl(1, "Unemployed", 0, perf=0),
        ],
    ),
}


def get_career(career_id: str) -> CareerDef | None:
    return CAREER_CATALOGUE.get(career_id)


def career_from_job_title(job_title: str) -> str:
    """Map a profile job title string to a career_id."""
    title_lower = job_title.lower()
    mapping = {
        ("engineer", "developer", "programmer", "software", "coder", "tech"): "tech",
        ("doctor", "nurse", "physician", "surgeon", "medical", "dentist"): "medicine",
        ("lawyer", "attorney", "legal", "paralegal", "judge"): "law",
        ("manager", "executive", "ceo", "cfo", "business", "analyst"): "business",
        ("artist", "painter", "musician", "singer", "performer", "dancer"): "arts",
        ("athlete", "sports", "player", "coach", "trainer"): "sports",
        ("chef", "cook", "baker", "culinary", "restaurant"): "culinary",
        ("criminal", "thief", "con", "hustler"): "criminal",
        ("politician", "senator", "mayor", "official"): "politics",
        ("teacher", "professor", "educator", "tutor"): "education",
        ("scientist", "researcher", "biologist", "physicist", "lab"): "science",
        ("journalist", "reporter", "writer", "blogger", "host", "media"): "media",
        ("soldier", "military", "officer", "general", "army"): "military",
        ("freelance", "freelancer", "contractor", "self-employed"): "freelance",
    }
    for keywords, career_id in mapping.items():
        if any(kw in title_lower for kw in keywords):
            return career_id
    return "unemployed"
