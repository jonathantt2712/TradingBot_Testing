'use client'
import { useState } from 'react'
import { Lock } from 'lucide-react'
import { ProfileCard } from './ProfileCard'
import { ChangePasswordModal } from './ChangePasswordModal'

export function SecurityCard() {
  const [open, setOpen] = useState(false)

  return (
    <ProfileCard title="Security" icon={Lock} iconColor="text-brand-purple">
      <button onClick={() => setOpen(true)} className="btn-primary text-xs">
        Change Password
      </button>
      {open && <ChangePasswordModal onClose={() => setOpen(false)} />}
    </ProfileCard>
  )
}
