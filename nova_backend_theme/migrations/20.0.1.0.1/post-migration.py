# Part of Nova. See LICENSE file for full copyright and licensing details.
#
# 20.0.1.0.1: the default accent moved Nova Blue (#0866FF) -> Teal (#0E7C8A).
# Rows still sitting on the old default never chose it explicitly, so roll
# them forward. Explicit non-default choices are left untouched.


def migrate(cr, version):
    cr.execute(
        "UPDATE res_company SET nova_accent = '#0E7C8A' "
        "WHERE nova_accent = '#0866FF'"
    )
    cr.execute(
        "UPDATE res_users SET nova_accent = '#0E7C8A' "
        "WHERE nova_accent = '#0866FF'"
    )
