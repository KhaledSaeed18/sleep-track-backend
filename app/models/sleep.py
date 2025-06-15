from tortoise.models import Model
from tortoise import fields

class SleepSession(Model):
    id = fields.IntField(pk=True)
    user = fields.ForeignKeyField("models.User", related_name="sessions")
    sleep_start = fields.DatetimeField()
    sleep_end = fields.DatetimeField()
    quality_rating = fields.IntField(null=True)  # 1 to 5
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "sleep_sessions"
