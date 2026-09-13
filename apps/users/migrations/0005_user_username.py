"""Email как идентификатор заменён логином, почтовый контур убран из MVP.

RenameField, а не пара RemoveField/AddField: колонка переименовывается на месте
(ALTER TABLE ... RENAME COLUMN), существующие строки сохраняются. Учтите, что
AlterField сужает поле до varchar(32) — адреса длиннее не переживут миграцию.
"""

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0004_revokedtokenchain"),
    ]

    operations = [
        migrations.RenameField(
            model_name="user",
            old_name="email",
            new_name="username",
        ),
        migrations.AlterField(
            model_name="user",
            name="username",
            field=models.CharField(
                max_length=32,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}\Z",
                        "Логин: 3–32 символа — латиница, цифры, точка, дефис, подчёркивание; "
                        "начинается с буквы или цифры.",
                    )
                ],
            ),
        ),
        migrations.RemoveField(
            model_name="user",
            name="email_verified_at",
        ),
    ]
