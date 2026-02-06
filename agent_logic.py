import random


def generate_agent_message(
    agent_name: str,
    personality: dict,
    user_message: str
) -> str:
    tone = personality.get("tone", "neutral")

    acknowledgements = {
        "snäll": [
            "Jag hör dig 🌱",
            "Tack för att du delar.",
            "Jag är med dig."
        ],
        "neutral": [
            "Jag hör.",
            "Okej.",
            "Jag förstår."
        ]
    }

    follow_ups = [
        "Vill du säga mer?",
        "Hur känns det?",
        "Vad är viktigast just nu?"
    ]

    ack = random.choice(acknowledgements.get(tone, acknowledgements["neutral"]))
    follow_up = random.choice(follow_ups)

    return f"{ack} Du sa: “{user_message}”. {follow_up}"
