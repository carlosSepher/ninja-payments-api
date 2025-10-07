-- Add optional customer RUT to payment orders
ALTER TABLE IF EXISTS payments.payment_order
  ADD COLUMN IF NOT EXISTS customer_rut text;
