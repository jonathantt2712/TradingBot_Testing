-- Owner/viewer roles for shared-bot controls. The FIRST account created is
-- assumed to be the operator and becomes owner; everyone else stays viewer.
-- (Adjust manually if that assumption is wrong for your database:
--   UPDATE "User" SET role='owner' WHERE email='you@example.com';)
ALTER TABLE "User" ADD COLUMN "role" TEXT NOT NULL DEFAULT 'viewer';
UPDATE "User" SET "role" = 'owner'
WHERE id = (SELECT id FROM "User" ORDER BY "createdAt" ASC LIMIT 1);
