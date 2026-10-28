# Loyalty Weekend Multiplier

## Purpose

Customers of a Loyalty Cards program earn more points at the weekend: the
points an order earns are multiplied by a Weekend Multiplier that the user
keys in on each program.

## Scope

- The weekend is Saturday and Sunday.
- One field, Weekend Multiplier, is added to the native `loyalty.program`
  (Loyalty Cards programs only; other program types are left untouched).
- It applies to every purchase: sales orders and Point of Sale orders.

The module extends the native loyalty models by inheritance only; it does not
copy or replace the native point computation.

## Dependencies

Odoo 20.0 Community modules only:

- `loyalty`: loyalty programs, rules and cards.
- `sale_loyalty`: loyalty points on sales orders.
- `pos_loyalty`: loyalty points on Point of Sale orders.
