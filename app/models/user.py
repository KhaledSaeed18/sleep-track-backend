from tortoise.models import Model
from tortoise import fields

class User(Model):
    id = fields.IntField(pk=True)
    email = fields.CharField(max_length=255, unique=True)
    first_name = fields.CharField(max_length=100)
    last_name = fields.CharField(max_length=100)
    date_of_birth = fields.DateField()
    hashed_password = fields.CharField(max_length=255)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "users"
