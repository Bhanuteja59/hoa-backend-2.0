"""Move phone to User

Revision ID: 0010_move_phone_to_user
Revises: 0009_add_arc_dates
Create Date: 2026-02-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0010_move_phone_to_user'
down_revision = '0009_add_arc_dates'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Add phone col to users
    op.add_column('users', sa.Column('phone', sa.String(length=50), nullable=True))
    op.create_index(op.f('ix_users_phone'), 'users', ['phone'], unique=True)

    # 2. Migrate data
    connection = op.get_bind()
    
    # Fetch existing phones from resident_profiles
    rows = connection.execute(sa.text("""
        SELECT DISTINCT ON (user_id) user_id, phone
        FROM resident_profiles
        WHERE phone IS NOT NULL AND phone != ''
        ORDER BY user_id, created_at DESC
    """)).fetchall()

    used_phones = set()
    
    for r in rows:
        uid = r[0]
        ph = r[1]
        if ph in used_phones:
            print(f"Skipping duplicate phone {ph} for user {uid}")
            continue
        
        used_phones.add(ph)
        connection.execute(sa.text("UPDATE users SET phone = :ph WHERE id = :uid"), {"ph": ph, "uid": uid})

    # 3. Drop column from resident_profiles
    op.drop_column('resident_profiles', 'phone')


def downgrade():
    # 1. Add column back to resident_profiles
    op.add_column('resident_profiles', sa.Column('phone', sa.String(length=50), nullable=True))
    
    # 2. Migrate data back
    connection = op.get_bind()
    connection.execute(sa.text("""
        UPDATE resident_profiles
        SET phone = users.phone
        FROM users
        WHERE resident_profiles.user_id = users.id
    """))

    # 3. Drop column from users
    op.drop_index(op.f('ix_users_phone'), table_name='users')
    op.drop_column('users', 'phone')
