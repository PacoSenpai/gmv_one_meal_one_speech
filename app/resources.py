from __future__ import annotations

from flask_restful import Resource
from marshmallow import Schema, fields
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import Talk, User, utc_now
from app.services.scheduling import next_scheduled_talk


class TalkResponseSchema(Schema):
    id = fields.Integer()
    title = fields.String()
    description = fields.String()
    event_at = fields.String()
    speaker = fields.String()
    image = fields.String(allow_none=True)


class HealthResource(Resource):
    def get(self) -> tuple[dict[str, str], int]:
        try:
            db.session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            db.session.rollback()
            return {"status": "unavailable"}, 503
        return {"status": "ok"}, 200


class PublicTalksResource(Resource):
    def get(self) -> tuple[dict[str, object], int]:
        talks = (
            Talk.query.filter(Talk.status == "scheduled", Talk.event_at > utc_now())
            .order_by(Talk.event_at.asc())
            .limit(100)
            .all()
        )
        payload = [
            {
                "id": talk.id,
                "title": talk.title,
                "description": talk.description,
                "event_at": talk.event_at.isoformat() + "Z",
                "speaker": talk.speaker.display_name,
                "image": f"/uploads/{talk.images[0].filename}" if talk.images else None,
            }
            for talk in talks
        ]
        return {"talks": TalkResponseSchema(many=True).dump(payload)}, 200


class PublicStatisticsResource(Resource):
    def get(self) -> tuple[dict[str, object], int]:
        speakers = (
            User.query.join(Talk)
            .filter(Talk.status == "completed")
            .all()
        )
        talks_given = []
        for speaker in speakers:
            count = sum(talk.status == "completed" for talk in speaker.talks)
            talks_given.append({"username": speaker.username, "name": speaker.display_name, "count": count})
        talks_given.sort(key=lambda row: (-int(row["count"]), str(row["name"]).casefold()))

        popular = []
        completed = Talk.query.filter_by(status="completed").all()
        for talk in completed:
            likes = sum(reaction.value == "like" for reaction in talk.reactions)
            dislikes = sum(reaction.value == "dislike" for reaction in talk.reactions)
            popular.append({"id": talk.id, "title": talk.title, "likes": likes, "dislikes": dislikes})
        popular.sort(
            key=lambda row: (
                -int(row["likes"]),
                -int(row["dislikes"]),
                str(row["title"]).casefold(),
            )
        )

        return {
            "speakers": talks_given,
            "popular_talks": popular[:10],
            "next_talk_id": next_talk.id if (next_talk := next_scheduled_talk()) else None,
        }, 200
