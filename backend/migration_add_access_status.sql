-- Migration: Add access_status column to users table
-- Run this against your production database

-- Add the access_status column with default 'approved'
ALTER TABLE users 
ADD COLUMN IF NOT EXISTS access_status VARCHAR(32) NOT NULL DEFAULT 'approved';

-- Set all existing users to 'approved' (this is idempotent)
UPDATE users 
SET access_status = 'approved' 
WHERE access_status IS NULL OR access_status = '';

-- Verify the migration
SELECT COUNT(*) as total_users, access_status 
FROM users 
GROUP BY access_status;
