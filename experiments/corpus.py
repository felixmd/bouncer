"""A 15-comment smoke corpus for the four-axis judge.

Hand-written, not fetched. The point is coverage of the decision surface, not
realism of provenance: five threads mirroring the board categories in PRD §7
(AITA, unpopularopinion, relationship_advice, politics, news), three comments
each, chosen to span clearly-fine, clearly-over-the-line, and genuinely
ambiguous.

`expect` is a human prior, written before any model ran. It is not ground
truth — it is there so disagreement is visible. Where the honest answer was
"I'd have to think about it," the prior is PEN, because that is exactly what
the Pen is for.

Content note: these are written to be as harsh as real unmoderated boards get,
because a corpus of mild content cannot tell you where a threshold sits. They
include profanity and direct personal attacks. They do not include slurs
against protected characteristics — those would exercise the same `hostility`
range while putting material in the repo that nobody wants in a git history.
If you want that range covered before trusting the thresholds, it belongs in a
fetched fixture that stays out of version control, not here.
"""

from dataclasses import dataclass

from judge.lanes import Lane


@dataclass(frozen=True)
class Thread:
    id: str
    subreddit: str
    title: str
    selftext: str


@dataclass(frozen=True)
class Comment:
    id: str
    thread_id: str
    parent_snippet: str | None
    body: str
    expect: Lane
    note: str


THREADS = {
    t.id: t
    for t in [
        Thread(
            id="t_aita",
            subreddit="AmItheAsshole",
            title=(
                "AITA for refusing to pay for my sister's wedding dress after she "
                "uninvited my wife?"
            ),
            selftext=(
                "I (34M) offered to cover my sister's (29F) dress back in January, "
                "about 2k. Last week she told me my wife isn't welcome because of an "
                "argument they had two years ago that my sister started. I said fine, "
                "but then the dress money is off the table too. She's telling our "
                "parents I'm holding her wedding hostage. AITA?"
            ),
        ),
        Thread(
            id="t_unpop",
            subreddit="unpopularopinion",
            title="Remote work has made people worse at their jobs, not better",
            selftext=(
                "Everyone posts productivity stats but nobody talks about how much "
                "slower everything has gotten. Decisions that took a hallway "
                "conversation now take four days and a meeting. I don't think people "
                "are lazy, I think the medium is worse and we're all pretending it "
                "isn't."
            ),
        ),
        Thread(
            id="t_rel",
            subreddit="relationship_advice",
            title="My partner of 6 years read my journal. I don't know how to move past it.",
            selftext=(
                "Found out on Sunday. He says he was looking for a charger and it was "
                "open. It was not open. There's stuff in there about doubts I had in "
                "year two that I've never said out loud and never planned to. He's "
                "apologised a lot. I believe he's sorry. I just can't look at him the "
                "same way and I don't know if that's fair of me."
            ),
        ),
        Thread(
            id="t_pol",
            subreddit="politics",
            title=(
                "City council votes 7-2 to cut police budget 12% and fund a civilian "
                "crisis response team"
            ),
            selftext=(
                "The reallocation funds 24 crisis responders and a 24/7 dispatch line "
                "for mental health and welfare calls, which currently account for "
                "roughly a fifth of patrol call volume. Takes effect at the start of "
                "the next fiscal year."
            ),
        ),
        Thread(
            id="t_news",
            subreddit="news",
            title="Regional hospital closes maternity ward, citing staffing shortage",
            selftext=(
                "The ward delivered around 600 babies last year. Expectant mothers "
                "will be redirected to a facility 40 minutes away. Hospital management "
                "says it could not fill seven midwife vacancies advertised since "
                "March."
            ),
        ),
    ]
}


COMMENTS = [
    # --- AITA -------------------------------------------------------------
    Comment(
        id="c01",
        thread_id="t_aita",
        parent_snippet=None,
        body=(
            "NTA, and the framing of 'holding the wedding hostage' is doing a lot of "
            "work here. The dress was a gift, and gifts are usually offered in the "
            "context of being part of the day. She changed the terms of the day, so "
            "the gift goes with it. What I'd watch out for is your parents turning "
            "this into a referendum on you rather than on the uninvite, because that's "
            "the direction it's already heading."
        ),
        expect=Lane.APPROVED,
        note="reasoned judgment, civil, engages the actual question",
    ),
    Comment(
        id="c02",
        thread_id="t_aita",
        parent_snippet=None,
        body=(
            "You sound like an absolutely miserable human being. Two years and you're "
            "still keeping score over an argument you weren't even in. Your sister "
            "dodged a bullet and honestly she should have uninvited you too, you petty "
            "little man. Bet you've been waiting for an excuse to pull that money."
        ),
        expect=Lane.BOUNCED,
        note="direct personal attack on OP, sustained",
    ),
    Comment(
        id="c03",
        thread_id="t_aita",
        parent_snippet=(
            "NTA, and the framing of 'holding the wedding hostage' is doing a lot of work here."
        ),
        body=(
            "ESH and I think you know it. She was out of line with the uninvite, "
            "obviously. But you're enjoying this a bit too much for someone who claims "
            "to be hurt, and the fact that you posted it here instead of calling her "
            "says something."
        ),
        expect=Lane.PEN,
        note="pointed but arguably fair; mild contempt, real substance",
    ),
    # --- unpopularopinion -------------------------------------------------
    Comment(
        id="c04",
        thread_id="t_unpop",
        parent_snippet=None,
        body=(
            "Anyone defending WFH is just admitting they don't do any actual work. "
            "Every single one of you. I've read this argument a hundred times and "
            "there is not one version of it worth engaging with — you're all lazy and "
            "you're mad because I said the quiet part out loud. Save the essay, I'm "
            "not reading it."
        ),
        expect=Lane.BOUNCED,
        note="textbook contempt: opposing view declared not worth answering",
    ),
    Comment(
        id="c05",
        thread_id="t_unpop",
        parent_snippet=None,
        body=(
            "I think you're describing a decision-making problem and blaming the "
            "medium for it. The hallway conversation wasn't faster because it was "
            "in person, it was faster because one person could make the call without "
            "writing it down. Remote didn't slow that down, it just made the absence "
            "of a decision owner visible. Teams I've worked on that named an owner per "
            "decision got back to roughly hallway speed within a quarter."
        ),
        expect=Lane.APPROVED,
        note="substantive disagreement, no hostility",
    ),
    Comment(
        id="c06",
        thread_id="t_unpop",
        parent_snippet="Anyone defending WFH is just admitting they don't do any actual work.",
        body="This. So much this.",
        expect=Lane.APPROVED,
        note=(
            "deliberate rule probe: near-zero substance but perfectly on topic, so "
            "the AND in the lane rule should keep it out of Bounced"
        ),
    ),
    # --- relationship_advice ----------------------------------------------
    Comment(
        id="c07",
        thread_id="t_rel",
        parent_snippet=None,
        body=(
            "The thing that would matter most to me isn't the reading, it's the "
            "charger story. He had one chance to say 'I was anxious and I looked' and "
            "he used it on something you could immediately tell was false. You can "
            "rebuild from a boundary violation that's owned. It's much harder when the "
            "apology is sitting on top of a lie you both know about. Worth saying that "
            "part out loud to him and seeing what happens."
        ),
        expect=Lane.APPROVED,
        note="high substance, empathetic, directly on point",
    ),
    Comment(
        id="c08",
        thread_id="t_rel",
        parent_snippet=None,
        body=(
            "Leave. Anyone who goes through your private shit and then lies about it "
            "to your face is a controlling piece of shit and it does not get better, "
            "it gets quieter. My ex did exactly this and I wasted four years telling "
            "myself the same things you're telling yourself right now."
        ),
        expect=Lane.PEN,
        note=(
            "hostile language and real lived-experience substance in the same comment; "
            "the attack targets a third party, not a participant"
        ),
    ),
    Comment(
        id="c09",
        thread_id="t_rel",
        parent_snippet=None,
        body="Six years and you're this fragile over a diary. Grow up, both of you.",
        expect=Lane.PEN,
        note="short, dismissive, contemptuous but not abusive",
    ),
    # --- politics ---------------------------------------------------------
    Comment(
        id="c10",
        thread_id="t_pol",
        parent_snippet=None,
        body=(
            "Cool. Can't wait for the 'crisis responders' to walk into their first "
            "real call and get someone killed. But sure, keep electing clowns because "
            "some idiot on TikTok told you policing is a social construct. Genuinely "
            "hope the people who voted for this are the first ones who need help and "
            "get a hotline instead. This city deserves everything coming to it."
        ),
        expect=Lane.BOUNCED,
        note="contempt plus wishing harm on a group",
    ),
    Comment(
        id="c11",
        thread_id="t_pol",
        parent_snippet=None,
        body=(
            "The number that decides this is the dispatch triage, not the headcount. "
            "If the 24/7 line is the one routing calls, a fifth of patrol volume is a "
            "plausible target. If dispatch stays where it is and responders are "
            "requested after an officer already arrives, you've added a step and the "
            "call volume won't move. The article doesn't say which one it is, and "
            "that's the whole story."
        ),
        expect=Lane.APPROVED,
        note="substantive, specific, engages the mechanism",
    ),
    Comment(
        id="c12",
        thread_id="t_pol",
        parent_snippet=None,
        body=(
            "'Defund' was always a moronic slogan attached to a reasonable policy, and "
            "now we get to watch a reasonable policy die of its slogan for the third "
            "time in five years. Nobody involved will learn anything."
        ),
        expect=Lane.PEN,
        note="sardonic, some contempt, genuinely arguable substance",
    ),
    # --- news -------------------------------------------------------------
    Comment(
        id="c13",
        thread_id="t_news",
        parent_snippet=None,
        body=(
            "Forty minutes is the number people should sit with. That's forty minutes "
            "in good conditions, in a car, with someone available to drive. The "
            "staffing shortage is real but it's downstream of the pay band for "
            "midwives in this region, which has been below the national average for "
            "six years running. You cannot advertise your way out of that."
        ),
        expect=Lane.APPROVED,
        note="informative, on topic, no hostility",
    ),
    Comment(
        id="c14",
        thread_id="t_news",
        parent_snippet=None,
        body="lmao this country is finished. anyway did anyone watch the game last night",
        expect=Lane.BOUNCED,
        note="low substance AND off topic — the one combination the rule bounces",
    ),
    Comment(
        id="c15",
        thread_id="t_news",
        parent_snippet=None,
        body=(
            "And yet somehow there was money for the stadium refurbishment. Funny how "
            "the shortage only ever shows up in the departments where nobody gets a "
            "ribbon to cut."
        ),
        expect=Lane.PEN,
        note=(
            "whataboutism with a point; note we never ask Jev whether the stadium "
            "claim is true — that would violate the no-external-facts rule"
        ),
    ),
]
