import { createFileRoute } from '@tanstack/react-router'
import { ProxyPoolPage } from '@/features/monitor/pages/proxy-pool'

export const Route = createFileRoute('/_authenticated/proxy-pool/')({
  component: ProxyPoolPage,
})
