def poem_about_aunt_kerri():
    # define subject
    aunt_kerri = {
        "name": "Kerri",
        "presence": "gentle",
        "spirit": "steadfast",
        "memory": "bright"
    }

    # emotional colors
    emotions = ["grief", "gratitude", "love"]

    # verses
    lines = []

    # her essence
    lines.append(f"{aunt_kerri['name']} carried light into dim corners")
    lines.append(f"{aunt_kerri['spirit']} echoed softly but never faded")

    # through time
    for season in ["spring", "summer", "autumn", "winter"]:
        lines.append(f"{season}: her laughter lingers on the breeze")

    # grief and gratitude intertwined
    if "grief" in emotions and "gratitude" in emotions:
        lines.append("we ache because we were loved")
        lines.append("we smile because we still are")

    # her legacy
    lines.append("her kindness is cached in our hearts")
    lines.append("her story persists, even in silence")

    return "\n".join(lines)

print(poem_about_aunt_kerri())
