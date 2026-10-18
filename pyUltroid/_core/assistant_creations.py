"""Reusable native RichMessage creations for assistant examples and commands."""

from __future__ import annotations

from telethon.tl import types

from pyUltroid._core.richmsg import rich_message


def _text(value: str):
    return types.TextPlain(value)


def _p(value: str):
    return types.PageBlockParagraph(_text(value))


def _button(label: str, kind):
    return types.PageButton(_text(label), kind)


def _rich_plan():
    return [
        types.PageBlockParagraph(types.TextBold(_text("Focused work plan"))),
        _p("A reusable template; fill in the blanks before acting."),
        types.PageBlockTable(
            _text("Plan at a glance"),
            [
                types.PageTableRow(
                    [
                        types.PageTableCell(header=True, text=_text("Stage")),
                        types.PageTableCell(header=True, text=_text("Prompt")),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("Goal")),
                        types.PageTableCell(text=_text("What outcome matters?")),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("Next")),
                        types.PageTableCell(
                            text=_text("What is the smallest useful step?")
                        ),
                    ]
                ),
            ],
            bordered=True,
            striped=True,
        ),
        types.PageBlockDetails(
            [
                _p("Define a measurable finish line and a timebox."),
                types.PageBlockPreformatted(
                    _text(
                        "Goal: [outcome]\nNext action: [verb + object]\nReview at: [time]"
                    ),
                    "text",
                ),
            ],
            _text("Planning worksheet"),
            open=False,
        ),
        types.PageBlockDetails(
            [
                types.PageBlockDetails(
                    [
                        types.PageBlockButtonRow(
                            [
                                _button(
                                    "Telegram docs",
                                    types.InlineButtonTypeUrl("https://telegram.org"),
                                ),
                                _button(
                                    "Copy plan",
                                    types.InlineButtonTypeCopy(
                                        "Goal: [outcome]\nNext action: [verb + object]"
                                    ),
                                ),
                            ]
                        )
                    ],
                    _text("Resources and reusable controls"),
                    open=False,
                )
            ],
            _text("Optional resources"),
            open=False,
        ),
    ]


def _decision_brief():
    return [
        types.PageBlockParagraph(types.TextBold(_text("Decision brief"))),
        _p(
            "A neutral structure for comparing options; no recommendation is prefilled."
        ),
        types.PageBlockTable(
            _text("Compare options"),
            [
                types.PageTableRow(
                    [
                        types.PageTableCell(header=True, text=_text("Criterion")),
                        types.PageTableCell(header=True, text=_text("Option A")),
                        types.PageTableCell(header=True, text=_text("Option B")),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("Benefits")),
                        types.PageTableCell(text=_text("[list]")),
                        types.PageTableCell(text=_text("[list]")),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("Risks")),
                        types.PageTableCell(text=_text("[list]")),
                        types.PageTableCell(text=_text("[list]")),
                    ]
                ),
            ],
            bordered=True,
            striped=True,
        ),
        types.PageBlockDetails(
            [
                _p("Check reversibility, evidence quality, and who is affected."),
                _p("Write what would change your mind."),
            ],
            _text("Evaluation checklist"),
            open=True,
        ),
        types.PageBlockDetails(
            [
                types.PageBlockDetails(
                    [
                        types.PageBlockButtonRow(
                            [
                                _button(
                                    "Telegram docs",
                                    types.InlineButtonTypeUrl("https://telegram.org"),
                                ),
                                _button(
                                    "Copy checklist",
                                    types.InlineButtonTypeCopy(
                                        "Benefits • Risks • Evidence • Reversibility"
                                    ),
                                ),
                            ]
                        )
                    ],
                    _text("Resources and reusable controls"),
                    open=False,
                )
            ],
            _text("Optional resources"),
            open=False,
        ),
    ]


def _meeting_agenda():
    return [
        types.PageBlockParagraph(types.TextBold(_text("Meeting agenda"))),
        _p("Template only — add attendees, timing, and decisions before sharing."),
        types.PageBlockList(
            [
                types.PageListItemText(
                    _text("State the desired outcome"), checkbox=True, checked=False
                ),
                types.PageListItemText(
                    _text("Review context and constraints"),
                    checkbox=True,
                    checked=False,
                ),
                types.PageListItemText(
                    _text("Agree owner and next action"), checkbox=True, checked=False
                ),
            ]
        ),
        types.PageBlockTable(
            _text("Agenda timing"),
            [
                types.PageTableRow(
                    [
                        types.PageTableCell(header=True, text=_text("Minutes")),
                        types.PageTableCell(header=True, text=_text("Topic")),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("5")),
                        types.PageTableCell(text=_text("Purpose and context")),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("10")),
                        types.PageTableCell(text=_text("Discussion")),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("5")),
                        types.PageTableCell(text=_text("Decision and actions")),
                    ]
                ),
            ],
            bordered=True,
        ),
        types.PageBlockDetails(
            [_p("Capture decision, owner, due date, and unresolved question.")],
            _text("Notes template"),
            open=False,
        ),
        types.PageBlockDetails(
            [
                types.PageBlockDetails(
                    [
                        types.PageBlockButtonRow(
                            [
                                _button(
                                    "Telegram docs",
                                    types.InlineButtonTypeUrl("https://telegram.org"),
                                ),
                                _button(
                                    "Copy action format",
                                    types.InlineButtonTypeCopy(
                                        "Action: [task] | Owner: [name] | Due: [date]"
                                    ),
                                ),
                            ]
                        )
                    ],
                    _text("Resources and reusable controls"),
                    open=False,
                )
            ],
            _text("Optional resources"),
            open=False,
        ),
    ]


def _learning_path():
    return [
        types.PageBlockParagraph(types.TextBold(_text("Learning path"))),
        _p("A practical study template, not a claim about your progress."),
        types.PageBlockDetails(
            [
                _p("Write one question you want to answer."),
                types.PageBlockPreformatted(
                    _text(
                        "Question: [specific question]\nI will know I understand when: [test]"
                    ),
                    "text",
                ),
            ],
            _text("1 · Set a target"),
            open=True,
        ),
        types.PageBlockTable(
            _text("Study cadence"),
            [
                types.PageTableRow(
                    [
                        types.PageTableCell(header=True, text=_text("Session")),
                        types.PageTableCell(header=True, text=_text("Focus")),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("Learn")),
                        types.PageTableCell(text=_text("One concept and source")),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("Practice")),
                        types.PageTableCell(text=_text("One small exercise")),
                    ]
                ),
            ],
            bordered=True,
            striped=True,
        ),
        types.PageBlockDetails(
            [_p("Read one reliable source, then explain the idea in your own words.")],
            _text("2 · Learn and explain"),
            open=False,
        ),
        types.PageBlockDetails(
            [_p("Try a small exercise and note what remains unclear.")],
            _text("3 · Practice and review"),
            open=False,
        ),
        types.PageBlockDetails(
            [
                types.PageBlockDetails(
                    [
                        types.PageBlockButtonRow(
                            [
                                _button(
                                    "Telegram docs",
                                    types.InlineButtonTypeUrl("https://telegram.org"),
                                ),
                                _button(
                                    "Copy study prompt",
                                    types.InlineButtonTypeCopy(
                                        "Teach-back: explain [idea] simply, then give one example."
                                    ),
                                ),
                            ]
                        )
                    ],
                    _text("Resources and reusable controls"),
                    open=False,
                )
            ],
            _text("Optional resources"),
            open=False,
        ),
    ]


def _project_review():
    return [
        types.PageBlockParagraph(types.TextBold(_text("Project review"))),
        _p("A reusable review card. Values below are prompts, not live project state."),
        types.PageBlockTable(
            _text("Review dimensions"),
            [
                types.PageTableRow(
                    [
                        types.PageTableCell(header=True, text=_text("Area")),
                        types.PageTableCell(header=True, text=_text("Prompt")),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("Progress")),
                        types.PageTableCell(
                            text=_text("What changed since last review?")
                        ),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("Risk")),
                        types.PageTableCell(
                            text=_text("What could block the next milestone?")
                        ),
                    ]
                ),
                types.PageTableRow(
                    [
                        types.PageTableCell(text=_text("Decision")),
                        types.PageTableCell(
                            text=_text("What needs an owner or choice?")
                        ),
                    ]
                ),
            ],
            bordered=True,
            striped=True,
        ),
        types.PageBlockDetails(
            [
                _p(
                    "Summarize evidence, not optimism. Separate confirmed facts from assumptions."
                ),
                types.PageBlockList(
                    [
                        types.PageListItemText(
                            _text("Name one next action"), checkbox=True, checked=False
                        ),
                        types.PageListItemText(
                            _text("Assign an owner and check-in"),
                            checkbox=True,
                            checked=False,
                        ),
                    ]
                ),
            ],
            _text("Review checklist"),
            open=False,
        ),
        types.PageBlockDetails(
            [
                types.PageBlockDetails(
                    [
                        types.PageBlockButtonRow(
                            [
                                _button(
                                    "Telegram docs",
                                    types.InlineButtonTypeUrl("https://telegram.org"),
                                ),
                                _button(
                                    "Copy review",
                                    types.InlineButtonTypeCopy(
                                        "Progress: [ ]\nRisk: [ ]\nDecision: [ ]\nNext action / owner: [ ]"
                                    ),
                                ),
                            ]
                        )
                    ],
                    _text("Resources and reusable controls"),
                    open=False,
                )
            ],
            _text("Optional resources"),
            open=False,
        ),
    ]


CREATIONS = (
    (
        "work-plan",
        "Focused work plan",
        "Table, details, URL and copy controls",
        _rich_plan,
    ),
    (
        "decision-brief",
        "Decision brief",
        "Comparison table, open details, controls",
        _decision_brief,
    ),
    (
        "meeting-agenda",
        "Meeting agenda",
        "Checklist, schedule table, details, controls",
        _meeting_agenda,
    ),
    (
        "learning-path",
        "Learning path",
        "Nested sections, code prompt and controls",
        _learning_path,
    ),
    (
        "project-review",
        "Project review",
        "Review table, checklist, details and controls",
        _project_review,
    ),
)


def creation_payload(sample_id: str):
    """Return one catalog template as an MTProto native rich payload."""
    for sid, _label, _description, builder in CREATIONS:
        if sid == sample_id:
            return rich_message(types.InputRichMessage(blocks=builder()))
    raise KeyError(sample_id)


def chessboard_html():
    """Return the starting position as an eight-row native HTML chessboard."""
    ranks = (
        "♜♞♝♛♚♝♞♜",
        "♟♟♟♟♟♟♟♟",
        "········",
        "········",
        "········",
        "········",
        "♙♙♙♙♙♙♙♙",
        "♖♘♗♕♔♗♘♖",
    )
    board = (
        '<table role="presentation" cellpadding="8" cellspacing="0" style="border-collapse:collapse;font-size:28px;text-align:center">'
        + "".join(
            "<tr>"
            + "".join(
                f'<td style="background:{"#f0d9b5" if (r + c) % 2 == 0 else "#b58863"};min-width:32px">{piece}</td>'
                for c, piece in enumerate(rank)
            )
            + "</tr>"
            for r, rank in enumerate(ranks)
        )
        + "</table>"
    )
    return board
