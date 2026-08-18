"""
Прогон всех тестов перед деплоем:  python tests/run_all.py
Код выхода 0 = всё зелёное, 1 = есть падения (не деплоить).
"""
import sys

import test_order_lifecycle
import test_messaging
import test_stats
import test_delivery
import test_gzip
import test_cache
import test_order_submit
import test_photos
import test_staff
import test_stock_alerts
import test_backup
import test_errors
import test_repeat_reminder
import test_prefill
import test_pickup_points
import test_profit
import test_free_delivery
import test_promos
import test_stock_moves
import test_customer_card
import test_gallery
import test_reviews
import test_categories
import test_upsell
import test_specs
import test_brands
import test_models
import test_seller_notify
import test_permissions
import test_order_edit
import test_hidden
import test_today

MODULES = [test_order_lifecycle, test_messaging, test_stats, test_delivery,
           test_gzip, test_cache, test_order_submit, test_photos, test_staff, test_stock_alerts, test_backup, test_errors, test_repeat_reminder, test_prefill, test_pickup_points, test_profit, test_free_delivery, test_promos, test_stock_moves, test_customer_card, test_gallery, test_reviews, test_categories, test_upsell, test_specs, test_brands, test_models, test_seller_notify, test_permissions, test_order_edit, test_hidden, test_today]


def main():
    all_fails = []
    for m in MODULES:
        all_fails += m.run()
        for extra in ('run_trust', 'run_settings', 'run_locations'):
            if hasattr(m, extra):
                all_fails += getattr(m, extra)()
    print("\n" + "=" * 40)
    if all_fails:
        print(f"❌ ПАДЕНИЙ: {len(all_fails)}")
        for f in all_fails:
            print("   •", f)
        return 1
    print("✅ ВСЕ ТЕСТЫ ПРОШЛИ — можно деплоить")
    return 0


if __name__ == "__main__":
    sys.exit(main())
