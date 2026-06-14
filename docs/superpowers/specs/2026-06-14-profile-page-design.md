# Profile Page Design

## Goal

Add a `/profile` page where a logged-in user can view their account info
(email, member-since date), edit their phone number, and change their
password via a modal. Replace the always-visible "Change Password" card
on the Settings page (built earlier this session, uncommitted) with this
single Profile entry point.

## Architecture & Data

- **Email is read-only.** The session's email/id is cached in the JWT at
  sign-in and not refreshed from the DB on subsequent requests, so
  allowing email edits would desync the session from the displayed value.
  Per explicit user instruction, no email-editing UI or backend is built.
- **Password change**: reuse the existing `POST /api/auth/change-password`
  route and its test file as-is (auth-gated, verifies `currentPassword`
  via `bcrypt.compare`, hashes `newPassword` with `bcrypt`, updates
  `User.passwordHash`). No changes to this route.
- **Phone change**: new `PATCH /api/auth/profile` route.
  - Auth-gated via `auth()` (same pattern as change-password route);
    401 if no `session.user.id`.
  - Body: `{ phone: string }`. Empty string clears the phone (sets to
    `null`); non-empty must match `/^\+?\d{6,15}$/` (same convention as
    the signup phone validation, which concatenates country code +
    digits into one string, e.g. `+972501234567`). Reject with 400 and
    an error message if invalid.
  - On success: `prisma.user.update({ where: { id: session.user.id },
    data: { phone } })`, return `{ success: true, phone }`.
- **Member since**: derived from `User.createdAt`, formatted as
  "Month YYYY" (e.g. "June 2026").
- The existing uncommitted `ChangePasswordCard` block in
  `app/settings/page.tsx` (and its `Lock`/`Eye`/`EyeOff` imports) is
  removed; its form/logic is relocated into a new
  `components/profile/ChangePasswordModal.tsx`.

## Page Layout (`/profile`)

Three cards, top to bottom, matching the existing Settings page's card
styling (rounded container, icon + heading header row):

1. **Header card**: circular avatar showing the first letter of the
   user's email (uppercase), the email address (read-only, with a small
   note "Contact support to change your email"), and "Member since
   <Month YYYY>".
2. **Account details card**: an editable phone number input pre-filled
   with the current value (or empty if `null`), with a "Save" button that
   calls `PATCH /api/auth/profile`. Shows a success/error toast (reusing
   the existing toast pattern from Settings) and updates the displayed
   value on success.
3. **Security card**: a single "Change Password" button. Clicking it opens
   `ChangePasswordModal`, styled like the existing `ConfirmModal`
   (fixed inset-0 backdrop with blur, centered card, click-outside-to-close,
   `animate-slide-up`), containing current/new/confirm password fields with
   a show/hide toggle and a submit button that posts to
   `/api/auth/change-password`. Success closes the modal and shows a toast.

## Navigation Integration

- **Sidebar (desktop)**: the footer currently shows the user's email as
  plain text, then a "Settings" link, a GitHub link, and a "Logout"
  button. Change the email text into a "Profile" link (still displaying
  the email as its label, or alongside it) using a `User` icon from
  lucide-react, placed immediately above "Settings".
- **MobileNav**: add a "Profile" tab using the `User` icon, placed next
  to the existing "Settings" tab (bringing the bottom bar to 7 items),
  following the same pattern used when Settings was added.

## Components & Files

- Create: `app/profile/page.tsx` — the three-card layout described above.
- Create: `components/profile/ChangePasswordModal.tsx` — modal version of
  the change-password form (reuses `/api/auth/change-password`).
- Create: `app/api/auth/profile/route.ts` — `PATCH` handler for phone
  updates.
- Create: `tests/api/profile.test.ts` — covers unauthenticated, invalid
  phone format, empty phone (clears to null), and successful update.
- Modify: `app/settings/page.tsx` — remove the `ChangePasswordCard`
  component and its render call, and remove the now-unused `Lock`, `Eye`,
  `EyeOff` imports (verify they aren't used elsewhere on the page first).
- Modify: `components/layout/Sidebar.tsx` — add "Profile" link in the
  footer and "Profile" tab in MobileNav, both using the `User` icon.

## Testing

- `tests/api/profile.test.ts`: new Vitest suite for the `PATCH` route,
  following the same mocking pattern as `tests/api/change-password.test.ts`
  (mock `@/auth` and `@/lib/prisma`).
- `tests/api/change-password.test.ts`: unchanged, still passes since the
  route is untouched.
- `npx tsc --noEmit` for type-checking after all edits.
- Manual/Playwright smoke test: navigate to `/profile`, verify header card
  shows correct email/avatar/member-since, edit and save phone, open the
  Change Password modal and complete a password change, and verify the
  new Sidebar/MobileNav "Profile" links navigate correctly.
