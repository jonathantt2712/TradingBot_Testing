import { cn } from '@/lib/utils'

interface ProfileCardProps {
  title: string
  icon: React.ElementType
  iconColor: string
  children: React.ReactNode
}

export function ProfileCard({ title, icon: Icon, iconColor, children }: ProfileCardProps) {
  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-bg-hover">
          <Icon className={cn('h-4 w-4', iconColor)} />
        </div>
        <h2 className="text-sm font-semibold text-primary">{title}</h2>
      </div>
      {children}
    </div>
  )
}
