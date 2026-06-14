import { redirect } from 'next/navigation'
import { User } from 'lucide-react'
import { auth } from '@/auth'
import { prisma } from '@/lib/prisma'
import { ProfileCard } from '@/components/profile/ProfileCard'
import { AccountDetailsCard } from '@/components/profile/AccountDetailsCard'
import { SecurityCard } from '@/components/profile/SecurityCard'
import { AlpacaAccountCard } from '@/components/profile/AlpacaAccountCard'

export default async function ProfilePage() {
  const session = await auth()
  if (!session?.user?.id) redirect('/login')

  const user = await prisma.user.findUnique({
    where: { id: session.user.id },
    select: { email: true, phone: true, createdAt: true },
  })
  if (!user) redirect('/login')

  const memberSince = user.createdAt.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

  return (
    <div className="px-4 py-4 md:px-6 md:py-6 space-y-4 md:space-y-6 max-w-[900px]">
      <div>
        <h1 className="text-xl font-bold text-primary">Profile</h1>
        <p className="text-xs text-muted mt-0.5">Account details and security</p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ProfileCard title="Account" icon={User} iconColor="text-brand-cyan">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-cyan/10 border border-brand-cyan/30 text-lg font-semibold text-brand-cyan">
              {user.email.charAt(0).toUpperCase()}
            </div>
            <div>
              <p className="text-sm font-medium text-primary">{user.email}</p>
              <p className="text-[11px] text-muted">Contact support to change your email</p>
            </div>
          </div>
          <p className="text-xs text-subtle">Member since {memberSince}</p>
        </ProfileCard>

        <AccountDetailsCard initialPhone={user.phone ?? ''} />

        <SecurityCard />

        <AlpacaAccountCard />
      </div>
    </div>
  )
}
