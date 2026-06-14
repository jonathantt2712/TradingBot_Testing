-- AlterTable
ALTER TABLE "User" DROP COLUMN "resetTokenExpiry",
DROP COLUMN "resetTokenHash",
ADD COLUMN     "mustChangePassword" BOOLEAN NOT NULL DEFAULT false;
