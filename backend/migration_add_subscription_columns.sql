-- Migration: Add subscription columns to users table
-- Run this against your production database

-- Add subscription_tier column
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS subscription_tier TEXT DEFAULT 'free'
CHECK (subscription_tier IN ('free', 'premium', 'lifetime', 'premium_student', 'ru', 'en', 'es', 'pt', 'de', 'it'));

-- Add subscription_status column
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS subscription_status TEXT DEFAULT NULL
CHECK (subscription_status IN ('active', 'past_due', 'canceled', 'trialing', NULL));

-- Add subscription_platform column
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS subscription_platform TEXT DEFAULT NULL
CHECK (subscription_platform IN ('paddle', 'apple', 'google', NULL));

-- Add subscription_external_id column (Paddle subscription_id, Apple original_transaction_id, Google purchase token)
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS subscription_external_id TEXT DEFAULT NULL;

-- Add subscription_started_at column
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS subscription_started_at TIMESTAMP DEFAULT NULL;

-- Add subscription_expires_at column (NULL for lifetime subscriptions)
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMP DEFAULT NULL;

-- Add subscription_cancel_at_period_end column
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS subscription_cancel_at_period_end BOOLEAN DEFAULT FALSE;

-- Add original_platform column (tracks where lifetime deal was purchased)
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS original_platform TEXT DEFAULT NULL
CHECK (original_platform IN ('paddle', 'apple', 'google', NULL));

-- Create unique index to prevent duplicate subscriptions
CREATE UNIQUE INDEX IF NOT EXISTS idx_subscription_external_id 
ON users(subscription_external_id)
WHERE subscription_external_id IS NOT NULL;

-- Verify the migration
SELECT COUNT(*) as total_users, subscription_tier, subscription_status 
FROM users 
GROUP BY subscription_tier, subscription_status;
