# Loyalty Weekend Multiplier

## Purpose

Customers of a Loyalty Cards program earn more points at the weekend: the
points an order earns are multiplied by a Weekend Multiplier that the user
keys in on each program.

## Scope

- The weekend is Saturday and Sunday.
- One field, Weekend Multiplier, is added to the native `loyalty.program`
  (Loyalty Cards programs only; other program types are left untouched).
- The shared weekend rule (`_lwm_weekend_points`) applies to every sales channel.

The module extends the native loyalty models by inheritance only; it does not
copy or replace the native point computation.

## Modules

- `loyalty_weekend_multiplier` (core): depends on `loyalty` only. Holds the
  Weekend Multiplier field, its form view and the shared weekend rule.
- `loyalty_weekend_multiplier_sale` (bridge): sales orders, installs itself
  when Sales is present.
- `loyalty_weekend_multiplier_pos` (bridge): Point of Sale, installs itself
  when Point of Sale is present.

## Upgrade

Existing databases are upgraded in place: the field and its values stay in the
core, whose name is unchanged. Installing the bridges of the apps present is
done by the upgrade script.

## Dependencies

Odoo 20.0 Community modules only: `loyalty`.
