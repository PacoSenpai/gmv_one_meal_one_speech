from __future__ import annotations

import calendar
from collections import Counter
from datetime import date, datetime, time

from flask import Blueprint, abort, current_app, g, render_template, request, send_from_directory

from app.extensions import db
from app.models import Comment, Reaction, Talk, User, utc_now
from app.services.dates import app_timezone, local_datetime, to_utc_naive
from app.services.scheduling import is_evaluation_open, next_scheduled_talk

public_bp = Blueprint("public", __name__)
SPANISH_MONTHS = [
    "",
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


@public_bp.get("/")
def index():
    upcoming = (
        Talk.query.filter(Talk.status == "scheduled", Talk.event_at > utc_now())
        .order_by(Talk.event_at.asc())
        .limit(8)
        .all()
    )
    recent_talks = (
        Talk.query.filter_by(status="completed")
        .order_by(Talk.completed_at.desc())
        .limit(3)
        .all()
    )
    return render_template(
        "public/index.html",
        next_talk=next_scheduled_talk(),
        talks=upcoming,
        recent_talks=recent_talks,
    )


@public_bp.get("/charlas/<int:talk_id>")
def talk_detail(talk_id: int):
    talk = db.get_or_404(Talk, talk_id)
    can_manage_own_talk = bool(
        g.current_user
        and talk.speaker_id == g.current_user.id
        and talk.status == "scheduled"
        and talk.event_at > utc_now()
    )
    following_talk = None
    if can_manage_own_talk:
        following_talk = (
            Talk.query.filter(Talk.status == "scheduled", Talk.event_at > talk.event_at)
            .order_by(Talk.event_at.asc())
            .first()
        )
    comments = (
        Comment.query.filter_by(talk_id=talk.id, parent_id=None)
        .order_by(Comment.created_at.asc())
        .all()
    )
    reaction = None
    if g.current_user:
        reaction = Reaction.query.filter_by(talk_id=talk.id, user_id=g.current_user.id).first()
    return render_template(
        "talks/detail.html",
        talk=talk,
        comments=comments,
        evaluation_open=is_evaluation_open(talk),
        current_reaction=reaction.value if reaction else None,
        likes=sum(item.value == "like" for item in talk.reactions),
        dislikes=sum(item.value == "dislike" for item in talk.reactions),
        can_manage_own_talk=can_manage_own_talk,
        following_talk=following_talk,
    )


@public_bp.get("/charlas")
def talk_archive():
    category = request.args.get("tipo", "proximas")
    if category not in {"proximas", "realizadas"}:
        abort(400)
    try:
        page_number = max(1, int(request.args.get("page", 1)))
    except ValueError:
        abort(400)

    if category == "proximas":
        query = (
            Talk.query.filter(Talk.status == "scheduled", Talk.event_at > utc_now())
            .order_by(Talk.event_at.asc())
        )
        heading = "Próximas exposiciones"
        intro = "Las ideas que ya tienen fecha y las voces que vienen."
    else:
        query = Talk.query.filter_by(status="completed").order_by(Talk.completed_at.desc())
        heading = "Conversaciones realizadas"
        intro = "Charlas que ya compartimos y a las que puedes volver."
    pagination = query.paginate(page=page_number, per_page=9, error_out=False)
    return render_template(
        "public/archive.html",
        category=category,
        heading=heading,
        intro=intro,
        pagination=pagination,
        scheduled_count=Talk.query.filter(Talk.status == "scheduled", Talk.event_at > utc_now()).count(),
        completed_count=Talk.query.filter_by(status="completed").count(),
    )


@public_bp.get("/agenda")
def agenda():
    today = datetime.now(app_timezone()).date()
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
        if not 1 <= month <= 12 or not 2000 <= year <= 2100:
            raise ValueError
    except (TypeError, ValueError):
        abort(400)

    month_start = datetime.combine(date(year, month, 1), time.min, app_timezone())
    if month == 12:
        next_month = datetime(year + 1, 1, 1, tzinfo=app_timezone())
    else:
        next_month = datetime(year, month + 1, 1, tzinfo=app_timezone())
    talks = (
        Talk.query.filter(
            Talk.status == "scheduled",
            Talk.event_at >= to_utc_naive(month_start),
            Talk.event_at < to_utc_naive(next_month),
        )
        .order_by(Talk.event_at.asc())
        .all()
    )
    month_rows = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    if month == 1:
        previous_year, previous_month = year - 1, 12
    else:
        previous_year, previous_month = year, month - 1
    if month == 12:
        next_year, following_month = year + 1, 1
    else:
        next_year, following_month = year, month + 1
    talks_by_day: dict[int, list[Talk]] = {}
    for talk in talks:
        local_day = local_datetime(talk.event_at).day
        talks_by_day.setdefault(local_day, []).append(talk)

    return render_template(
        "public/agenda.html",
        year=year,
        month=month,
        month_name=SPANISH_MONTHS[month],
        today=today,
        month_rows=month_rows,
        talks_by_day=talks_by_day,
        talks=talks,
        previous_year=previous_year,
        previous_month=previous_month,
        next_year=next_year,
        next_month=following_month,
    )


@public_bp.get("/estadisticas")
def statistics():
    completed_talks = Talk.query.filter_by(status="completed").all()
    speaker_counts = Counter(talk.speaker_id for talk in completed_talks)
    speaker_ids = [speaker_id for speaker_id, _ in speaker_counts.most_common()]
    speakers = (
        {user.id: user for user in User.query.filter(User.id.in_(speaker_ids)).all()}
        if speaker_ids
        else {}
    )
    speaker_ranking = [
        {"speaker": speakers[speaker_id], "count": count}
        for speaker_id, count in speaker_counts.most_common()
        if speaker_id in speakers
    ]

    popular_talks = []
    for talk in completed_talks:
        popular_talks.append(
            {
                "talk": talk,
                "likes": sum(reaction.value == "like" for reaction in talk.reactions),
                "dislikes": sum(reaction.value == "dislike" for reaction in talk.reactions),
            }
        )
    popular_talks.sort(key=lambda item: (-item["likes"], item["talk"].title.casefold()))
    return render_template(
        "public/statistics.html",
        speaker_ranking=speaker_ranking,
        popular_talks=popular_talks[:10],
        total_talks=len(completed_talks),
        total_members=User.query.filter_by(role="member", is_active=True).count(),
    )


@public_bp.get("/uploads/<path:filename>")
def uploaded_file(filename: str):
    if "/" in filename or "\\" in filename or not filename.endswith(".webp"):
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename, max_age=3600)
