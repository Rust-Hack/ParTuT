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
import test_access_holes
import test_race
import test_user_names
import test_card_order_notify
import test_my_settings
import test_unpaid_expire
import test_cross_city
import test_cart_shortage
import test_junk_input
import test_promo_race
import test_privacy
import test_games_race
import test_referral_abuse
import test_migration
import test_bot_access
import test_loyalty
import test_background
import test_cleanup
import test_double_order
import test_raffle
import test_compensation
import test_shop_time
import test_till
import test_smoke_routes
import test_module_split
import test_server_split
import test_schema
import test_webapp

MODULES = [test_order_lifecycle, test_messaging, test_stats, test_delivery,
           test_gzip, test_cache, test_order_submit, test_photos, test_staff, test_stock_alerts, test_backup, test_errors, test_repeat_reminder, test_prefill, test_pickup_points, test_profit, test_free_delivery, test_promos, test_stock_moves, test_customer_card, test_gallery, test_reviews, test_categories, test_upsell, test_specs, test_brands, test_models, test_seller_notify, test_permissions, test_order_edit, test_hidden, test_today, test_access_holes, test_race, test_user_names, test_card_order_notify, test_my_settings, test_unpaid_expire, test_cross_city, test_cart_shortage, test_junk_input, test_promo_race, test_privacy, test_games_race, test_referral_abuse, test_migration, test_bot_access, test_loyalty, test_background, test_cleanup, test_double_order, test_raffle, test_compensation, test_shop_time, test_till, test_smoke_routes, test_module_split, test_server_split, test_schema, test_webapp]


def main():
    all_fails = []
    for m in MODULES:
        all_fails += m.run()
        for extra in ('run_trust', 'run_settings', 'run_locations', 'run_phone', 'run_handler_order', 'run_daily_once', 'run_empty_restore', 'run_etag', 'run_standalone', 'run_upgrade_existing'):
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
