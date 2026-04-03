-- Migration 005: Add stripe_customer_id to users table
-- Run in Supabase SQL editor

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT UNIQUE;
