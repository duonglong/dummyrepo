# Loyalty Weekend Multiplier - Sales

Bridge between `loyalty_weekend_multiplier` and `sale_loyalty`.

Sales orders (quotations and website shop orders) earn the points of their
Loyalty Cards programs multiplied by the program's Weekend Multiplier when the
order falls on a Saturday or Sunday. A quotation confirmed on another day is
re-evaluated with the confirmation day. Reward costs are never multiplied.

## Dependencies

- `loyalty_weekend_multiplier`: the Weekend Multiplier field and the weekend rule.
- `sale_loyalty`: loyalty points on sales orders.

The module installs itself automatically when both are installed. It does not
need Point of Sale.
