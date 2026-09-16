import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Boxes,
  Clock,
  Download,
  Eye,
  KeyRound,
  Link2,
  Loader2,
  Plus,
  RefreshCw,
  Server,
  Settings2,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'
import {
  api,
  type ProxyPoolConfigInput,
  type ProxyPoolGroup,
  type ProxyPoolImportResult,
  type ProxyPoolMode,
  type ProxyPoolPreview,
  type ProxyPoolScheme,
} from '@/lib/api'
import { cn, formatDate, getErrorMessage } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { ActionToolbar, ToolbarAction } from '@/components/action-toolbar'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { InfoTooltip } from '@/components/info-tooltip'
import { EmptyState, LoadingState, Page, PageHeader } from '@/components/page'
import { SelectionToolbar } from '@/components/selection-toolbar'
import { TablePanel } from '@/components/table-panel'
import { TitledCard } from '@/components/titled-card'

type ConfigForm = {
  apiUrlTemplate: string
  resinBaseUrl: string
  resinAdminToken: string
  groupSize: string
  targetIpCount: string
  leaseHours: string
  scheme: ProxyPoolScheme
  subscriptionPrefix: string
  autoRefreshEnabled: boolean
  mode: ProxyPoolMode
  gatewayHost: string
  gatewayPort: string
  gatewayUsername: string
  gatewayPassword: string
  gatewayRegion: string
  gatewaySticky: string
  overFactor: string
}

const emptyConfigForm: ConfigForm = {
  apiUrlTemplate: '',
  resinBaseUrl: '',
  resinAdminToken: '',
  groupSize: '50',
  targetIpCount: '50',
  leaseHours: '24',
  scheme: 'socks5',
  subscriptionPrefix: 'grokiq-1024',
  autoRefreshEnabled: false,
  mode: 'url',
  gatewayHost: '',
  gatewayPort: '3000',
  gatewayUsername: '',
  gatewayPassword: '',
  gatewayRegion: 'SG',
  gatewaySticky: '1',
  overFactor: '2',
}

function positiveInt(value: string): number | undefined {
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed <= 0) return undefined
  return Math.trunc(parsed)
}

export function ProxyPoolPage() {
  const queryClient = useQueryClient()
  const [configOpen, setConfigOpen] = useState(false)
  const [importOpen, setImportOpen] = useState(false)
  const [deleteIds, setDeleteIds] = useState<number[] | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [configForm, setConfigForm] = useState<ConfigForm>(emptyConfigForm)
  const [clearApiUrl, setClearApiUrl] = useState(false)
  const [clearAdminToken, setClearAdminToken] = useState(false)
  const [clearGatewayPassword, setClearGatewayPassword] = useState(false)
  const [totalOverride, setTotalOverride] = useState('')
  const [preview, setPreview] = useState<ProxyPoolPreview | null>(null)
  const [lastImport, setLastImport] = useState<ProxyPoolImportResult | null>(
    null
  )

  const configQuery = useQuery({
    queryKey: ['proxy-pool-config'],
    queryFn: api.proxyPoolConfig,
  })
  const groupsQuery = useQuery({
    queryKey: ['proxy-pool-groups'],
    queryFn: api.proxyPoolGroups,
    refetchInterval: 30_000,
  })
  const config = configQuery.data
  const groups = groupsQuery.data?.groups ?? []
  const selectedSet = useMemo(() => new Set(selected), [selected])
  const selectedGroups = groups.filter((group) => selectedSet.has(group.id))
  const allChecked =
    groups.length > 0 && groups.every((group) => selectedSet.has(group.id))
  const chosenTotal = positiveInt(totalOverride)

  const saveConfigMutation = useMutation({
    mutationFn: (payload: ProxyPoolConfigInput) =>
      api.updateProxyPoolConfig(payload),
    onSuccess: (result) => {
      setConfigOpen(false)
      setClearApiUrl(false)
      setClearAdminToken(false)
      toast.success('代理池配置已保存')
      queryClient.setQueryData(['proxy-pool-config'], result.config)
      void queryClient.invalidateQueries({ queryKey: ['proxy-pool-groups'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const previewMutation = useMutation({
    mutationFn: (total?: number) => api.previewProxyPool(total),
    onSuccess: (result) => setPreview(result),
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const importMutation = useMutation({
    mutationFn: () => api.importProxyPool(chosenTotal),
    onSuccess: (result) => {
      setImportOpen(false)
      setPreview(null)
      setLastImport(result)
      if (result.failed > 0) {
        toast.warning(
          `已导入 ${result.created + result.updated} 组，${result.failed} 组失败`
        )
      } else {
        toast.success(
          `已导入 ${result.groupCount} 组，共 ${result.fetched} 个 IP`
        )
      }
      void queryClient.invalidateQueries({ queryKey: ['proxy-pool-groups'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const refreshAllMutation = useMutation({
    mutationFn: () => api.refreshProxyPool(),
    onSuccess: (result) => {
      setLastImport(result)
      toast.success(`已刷新 ${result.updated} 组，共 ${result.fetched} 个 IP`)
      void queryClient.invalidateQueries({ queryKey: ['proxy-pool-groups'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const refreshGroupMutation = useMutation({
    mutationFn: (groupId: number) => api.refreshProxyPoolGroup(groupId),
    onSuccess: ({ group }) => {
      toast.success(`${group.name} 已刷新 ${group.size} 个 IP`)
      void queryClient.invalidateQueries({ queryKey: ['proxy-pool-groups'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const deleteMutation = useMutation({
    mutationFn: (ids: number[]) => api.deleteProxyPoolGroups(ids),
    onSuccess: (result) => {
      setDeleteIds(null)
      setSelected([])
      const failed = result.remoteErrors.length
      if (failed) {
        toast.warning(
          `已移除 ${result.deleted} 组本地记录，${failed} 组 Resin 删除失败`
        )
      } else {
        toast.success(`已删除 ${result.deleted} 组并清理 Resin 订阅`)
      }
      void queryClient.invalidateQueries({ queryKey: ['proxy-pool-groups'] })
    },
    onError: (error) => toast.error(getErrorMessage(error)),
  })

  const actionPending =
    importMutation.isPending ||
    refreshAllMutation.isPending ||
    refreshGroupMutation.isPending ||
    deleteMutation.isPending
  const showTableLoading = groupsQuery.isFetching

  const openConfig = () => {
    setConfigForm({
      apiUrlTemplate: '',
      resinBaseUrl: config?.resinBaseUrl ?? '',
      resinAdminToken: '',
      groupSize: String(config?.groupSize ?? 50),
      targetIpCount: String(config?.targetIpCount ?? 50),
      leaseHours: String(config?.leaseHours ?? 24),
      scheme: config?.scheme ?? 'socks5',
      subscriptionPrefix: config?.subscriptionPrefix ?? 'grokiq-1024',
      autoRefreshEnabled: config?.autoRefreshEnabled ?? false,
      mode: config?.mode ?? 'url',
      gatewayHost: config?.gatewayHost ?? '',
      gatewayPort: String(config?.gatewayPort ?? 3000),
      gatewayUsername: config?.gatewayUsername ?? '',
      gatewayPassword: '',
      gatewayRegion: config?.gatewayRegion ?? 'SG',
      gatewaySticky: config?.gatewaySticky ?? '1',
      overFactor: String(config?.overFactor ?? 2),
    })
    setClearApiUrl(false)
    setClearAdminToken(false)
    setClearGatewayPassword(false)
    setConfigOpen(true)
  }

  const revealSecrets = async () => {
    try {
      const [template, token, password] = await Promise.all([
        api.revealSettingSecret('proxyPoolApiUrlTemplate'),
        api.revealSettingSecret('proxyPoolResinAdminToken'),
        api.revealSettingSecret('proxyPoolGatewayPassword'),
      ])
      setConfigForm((current) => ({
        ...current,
        apiUrlTemplate: template.value,
        resinAdminToken: token.value,
        gatewayPassword: password.value,
      }))
      setClearApiUrl(false)
      setClearAdminToken(false)
      setClearGatewayPassword(false)
    } catch (error) {
      toast.error(getErrorMessage(error))
    }
  }

  const submitConfig = () => {
    const payload: ProxyPoolConfigInput = {
      resinBaseUrl: configForm.resinBaseUrl.trim(),
      groupSize: positiveInt(configForm.groupSize),
      targetIpCount: positiveInt(configForm.targetIpCount),
      leaseHours: positiveInt(configForm.leaseHours),
      scheme: configForm.scheme,
      subscriptionPrefix: configForm.subscriptionPrefix.trim(),
      autoRefreshEnabled: configForm.autoRefreshEnabled,
      mode: configForm.mode,
      gatewayHost: configForm.gatewayHost.trim(),
      gatewayPort: positiveInt(configForm.gatewayPort),
      gatewayUsername: configForm.gatewayUsername.trim(),
      gatewayRegion: configForm.gatewayRegion.trim(),
      gatewaySticky: configForm.gatewaySticky.trim(),
      overFactor: positiveInt(configForm.overFactor),
    }
    const template = configForm.apiUrlTemplate.trim()
    if (template) payload.apiUrlTemplate = template
    else if (clearApiUrl) payload.apiUrlTemplate = ''
    const token = configForm.resinAdminToken.trim()
    if (token) payload.resinAdminToken = token
    else if (clearAdminToken) payload.resinAdminToken = ''
    const password = configForm.gatewayPassword.trim()
    if (password) payload.gatewayPassword = password
    else if (clearGatewayPassword) payload.gatewayPassword = ''
    saveConfigMutation.mutate(payload)
  }

  const openImport = () => {
    const initial = String(config?.targetIpCount ?? 50)
    setTotalOverride(initial)
    setPreview(null)
    setImportOpen(true)
    void previewMutation.mutateAsync(positiveInt(initial))
  }

  return (
    <Page>
      <PageHeader
        title='代理池'
        description='从 1024proxy 提取短效住宅 IP，按每 50 个一组写入 Resin 订阅，到期后自动重拉刷新。'
        descriptionAsHint
        actions={
          <ActionToolbar label='代理池操作'>
            <ToolbarAction
              label='代理池配置'
              disabled={actionPending}
              onClick={openConfig}
            >
              <Settings2 />
            </ToolbarAction>
            <ToolbarAction
              label='提取并导入'
              disabled={actionPending}
              onClick={openImport}
            >
              <Download />
            </ToolbarAction>
            <ToolbarAction
              label='立即刷新全部租约'
              disabled={actionPending || groups.length === 0}
              pending={refreshAllMutation.isPending}
              onClick={() => refreshAllMutation.mutate()}
            >
              <RefreshCw />
            </ToolbarAction>
            <ToolbarAction
              label='刷新列表'
              pending={groupsQuery.isFetching}
              onClick={() => void groupsQuery.refetch()}
            >
              <RefreshCw />
            </ToolbarAction>
            <SelectionToolbar
              wrap={false}
              selectedCount={selected.length}
              entityLabel='分组'
              disabled={actionPending}
              onClear={() => setSelected([])}
            >
              <ToolbarAction
                label={`删除 ${selected.length} 个已选分组`}
                destructive
                disabled={actionPending}
                onClick={() =>
                  setDeleteIds(selectedGroups.map((group) => group.id))
                }
              >
                <Trash2 />
              </ToolbarAction>
            </SelectionToolbar>
          </ActionToolbar>
        }
      />

      <TitledCard
        icon={<Server />}
        title='连接与租约'
        description='1024proxy 提取链接、Resin 地址与分组租约参数；密钥不回显。'
        action={
          <Button variant='outline' size='sm' onClick={openConfig}>
            <Settings2 />
            配置
          </Button>
        }
      >
        {configQuery.isLoading && !config ? (
          <LoadingState label='正在读取代理池配置' />
        ) : configQuery.isError ? (
          <p className='text-sm text-destructive'>
            配置读取失败：{getErrorMessage(configQuery.error)}
          </p>
        ) : (
          <div className='grid gap-3 sm:grid-cols-2 xl:grid-cols-4'>
            <ConfigMetric
              icon={<Link2 />}
              label='取号方式'
              value={config?.mode === 'gateway' ? '账密网关 + sid' : '提取链接'}
              ok
            />
            <ConfigMetric
              icon={<Link2 />}
              label='取号来源'
              value={
                config?.mode === 'gateway'
                  ? `${config?.gatewayHost || '未配置'}:${config?.gatewayPort ?? ''}`
                  : config?.apiUrlTemplateConfigured
                    ? '已配置'
                    : '未配置'
              }
              ok={
                config?.mode === 'gateway'
                  ? Boolean(
                      config?.gatewayHost && config?.gatewayPasswordConfigured
                    )
                  : config?.apiUrlTemplateConfigured
              }
            />
            <ConfigMetric
              icon={<Server />}
              label='Resin 地址'
              value={config?.resinBaseUrl || '未配置'}
              ok={Boolean(config?.resinBaseUrl)}
            />
            <ConfigMetric
              icon={<KeyRound />}
              label='Resin Admin Token'
              value={config?.resinAdminTokenConfigured ? '已配置' : '未配置'}
              ok={config?.resinAdminTokenConfigured}
            />
            <ConfigMetric
              icon={<Boxes />}
              label='分组与租约'
              value={`每 ${config?.groupSize ?? 50} 个 / ${config?.leaseHours ?? 24}h / ${config?.scheme ?? 'socks5'}`}
              ok
            />
            <ConfigMetric
              icon={<Download />}
              label='提取总数'
              value={`${config?.targetIpCount ?? 0} 个 IP`}
              ok
            />
            <ConfigMetric
              icon={<Boxes />}
              label='订阅前缀'
              value={config?.subscriptionPrefix || '—'}
              ok={Boolean(config?.subscriptionPrefix)}
            />
            <ConfigMetric
              icon={<RefreshCw />}
              label='自动刷新'
              value={config?.autoRefreshEnabled ? '已开启' : '已关闭'}
              ok={config?.autoRefreshEnabled}
            />
            <ConfigMetric
              icon={<Clock />}
              label='当前分组'
              value={`${groups.length} 组`}
              ok
            />
          </div>
        )}
        {lastImport && (
          <p className='mt-3 text-xs text-muted-foreground'>
            最近一次导入：{lastImport.groupCount} 组，新增 {lastImport.created}
            、更新 {lastImport.updated}
            {lastImport.failed ? `、失败 ${lastImport.failed}` : ''}
            {lastImport.pruned ? `，清理 ${lastImport.pruned} 组` : ''}
          </p>
        )}
      </TitledCard>

      <TablePanel
        toolbar={
          <div className='flex items-center justify-between gap-2'>
            <div className='text-sm font-medium'>Resin 订阅分组</div>
            {showTableLoading && (
              <Loader2 className='size-4 animate-spin text-primary' />
            )}
          </div>
        }
      >
        {groupsQuery.isLoading && !groupsQuery.data ? (
          <LoadingState />
        ) : groupsQuery.isError ? (
          <EmptyState
            title='分组读取失败'
            description={getErrorMessage(groupsQuery.error)}
          />
        ) : groups.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className='w-10'>
                  <Checkbox
                    checked={allChecked}
                    onCheckedChange={(value) =>
                      setSelected(value === true ? groups.map((g) => g.id) : [])
                    }
                    aria-label='选择全部分组'
                  />
                </TableHead>
                <TableHead>分组</TableHead>
                <TableHead>Resin 订阅</TableHead>
                <TableHead className='text-center'>代理数</TableHead>
                <TableHead className='text-center'>状态</TableHead>
                <TableHead>租约到期</TableHead>
                <TableHead>最近刷新</TableHead>
                <TableHead className='text-right'>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {groups.map((group) => {
                const refreshing =
                  refreshGroupMutation.isPending &&
                  refreshGroupMutation.variables === group.id
                return (
                  <TableRow key={group.id}>
                    <TableCell>
                      <Checkbox
                        checked={selectedSet.has(group.id)}
                        onCheckedChange={(value) =>
                          setSelected((current) =>
                            value === true
                              ? Array.from(new Set([...current, group.id]))
                              : current.filter((id) => id !== group.id)
                          )
                        }
                        aria-label={`选择分组 ${group.name}`}
                      />
                    </TableCell>
                    <TableCell>
                      <div className='flex items-center gap-2'>
                        <span className='inline-flex size-7 shrink-0 items-center justify-center rounded-md bg-muted/70 text-muted-foreground'>
                          <Boxes className='size-4' />
                        </span>
                        <div className='min-w-0'>
                          <div className='truncate font-medium'>
                            {group.name}
                          </div>
                          <div className='mt-0.5 font-mono text-[11px] text-muted-foreground'>
                            #{group.index} · {group.scheme}
                          </div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className='max-w-56 truncate font-mono text-xs text-muted-foreground'>
                        {group.subscriptionId || '—'}
                      </div>
                    </TableCell>
                    <TableCell className='text-center tabular-nums'>
                      {group.size}
                    </TableCell>
                    <TableCell className='text-center'>
                      <StatusBadge group={group} />
                    </TableCell>
                    <TableCell className='text-sm text-muted-foreground'>
                      {group.leaseExpiresAt
                        ? formatDate(group.leaseExpiresAt)
                        : '—'}
                    </TableCell>
                    <TableCell className='text-sm text-muted-foreground'>
                      {group.lastRefreshedAt
                        ? formatDate(group.lastRefreshedAt)
                        : '—'}
                    </TableCell>
                    <TableCell className='text-right'>
                      <div className='inline-flex items-center gap-0.5'>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              size='icon'
                              variant='ghost'
                              className='size-7'
                              disabled={actionPending}
                              onClick={() =>
                                refreshGroupMutation.mutate(group.id)
                              }
                              aria-label={`刷新分组 ${group.name}`}
                            >
                              {refreshing ? (
                                <Loader2 className='animate-spin' />
                              ) : (
                                <RefreshCw />
                              )}
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>重新提取本组 IP</TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              size='icon'
                              variant='ghost'
                              className='size-7 text-muted-foreground hover:bg-destructive/10 hover:text-destructive'
                              disabled={actionPending}
                              onClick={() => setDeleteIds([group.id])}
                              aria-label={`删除分组 ${group.name}`}
                            >
                              <Trash2 />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>删除分组与 Resin 订阅</TooltipContent>
                        </Tooltip>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        ) : (
          <EmptyState
            title='还没有代理池分组'
            description='完成配置后点击「提取并导入」，每组 50 个 IP 会写入一个 Resin 订阅。'
            action={
              <Button variant='outline' onClick={openImport}>
                <Download />
                提取并导入
              </Button>
            }
          />
        )}
      </TablePanel>

      <Dialog
        open={configOpen}
        onOpenChange={(open) => {
          if (saveConfigMutation.isPending) return
          setConfigOpen(open)
        }}
      >
        <DialogContent className='sm:max-w-2xl'>
          <DialogHeader>
            <DialogTitle>代理池配置</DialogTitle>
            <DialogDescription>
              密钥采用只写方式保存，留空表示保持不变；需要查看时点击「载入已保存密钥」。
            </DialogDescription>
          </DialogHeader>
          <div className='grid max-h-[60vh] gap-3 overflow-y-auto pr-1 sm:grid-cols-2'>
            <div className='space-y-1.5 sm:col-span-2'>
              <div className='flex items-center gap-1.5'>
                <Label>取号方式</Label>
                <InfoTooltip
                  label='取号方式'
                  content={
                    <>
                      网关模式用账号密码连 1024 网关，靠随机 sid
                      生成稳定粘性节点， 更稳；提取链接模式走 dashboard 的 API
                      链接。
                    </>
                  }
                />
              </div>
              <Select
                value={configForm.mode}
                onValueChange={(value) =>
                  setConfigForm((current) => ({
                    ...current,
                    mode: value as ProxyPoolMode,
                  }))
                }
              >
                <SelectTrigger className='w-full'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value='gateway'>
                    账密网关 + 随机 sid（推荐）
                  </SelectItem>
                  <SelectItem value='url'>
                    1024proxy 提取链接（API 模式）
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            {configForm.mode === 'url' ? (
              <div className='space-y-1.5 sm:col-span-2'>
                <div className='flex min-h-5 items-center justify-between gap-2'>
                  <div className='flex items-center gap-1.5'>
                    <Label htmlFor='proxy-pool-template'>
                      1024proxy 提取链接模板
                    </Label>
                    <InfoTooltip
                      label='1024proxy 提取链接模板'
                      content={
                        <>
                          直接粘贴 dashboard API 模式生成的链接，把数量改成{' '}
                          <span className='font-mono'>{'{num}'}</span>{' '}
                          占位符，例如
                          <span className='font-mono'>
                            &amp;num={'{num}'}&amp;type=txt
                          </span>
                          。
                        </>
                      }
                    />
                  </div>
                  <Button
                    type='button'
                    size='sm'
                    variant='ghost'
                    className='h-6 px-2 text-xs'
                    disabled={!config?.apiUrlTemplateConfigured}
                    onClick={() => {
                      setClearApiUrl(true)
                      setConfigForm((current) => ({
                        ...current,
                        apiUrlTemplate: '',
                      }))
                    }}
                  >
                    清除已保存
                  </Button>
                </div>
                <Input
                  id='proxy-pool-template'
                  type='password'
                  autoComplete='off'
                  value={configForm.apiUrlTemplate}
                  onChange={(event) => {
                    setClearApiUrl(false)
                    setConfigForm((current) => ({
                      ...current,
                      apiUrlTemplate: event.target.value,
                    }))
                  }}
                  placeholder={
                    config?.apiUrlTemplateConfigured
                      ? '已配置，留空保持不变'
                      : 'https://api.1024proxy.com/...&num={num}&type=txt'
                  }
                />
              </div>
            ) : (
              <>
                <div className='space-y-1.5'>
                  <Label htmlFor='proxy-pool-gw-host'>网关地址</Label>
                  <Input
                    id='proxy-pool-gw-host'
                    value={configForm.gatewayHost}
                    onChange={(event) =>
                      setConfigForm((current) => ({
                        ...current,
                        gatewayHost: event.target.value,
                      }))
                    }
                    placeholder='us.1024proxy.io'
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label htmlFor='proxy-pool-gw-port'>网关端口</Label>
                  <Input
                    id='proxy-pool-gw-port'
                    type='number'
                    min={1}
                    max={65535}
                    value={configForm.gatewayPort}
                    onChange={(event) =>
                      setConfigForm((current) => ({
                        ...current,
                        gatewayPort: event.target.value,
                      }))
                    }
                    placeholder='3000'
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label htmlFor='proxy-pool-gw-user'>网关账号</Label>
                  <Input
                    id='proxy-pool-gw-user'
                    value={configForm.gatewayUsername}
                    onChange={(event) =>
                      setConfigForm((current) => ({
                        ...current,
                        gatewayUsername: event.target.value,
                      }))
                    }
                    placeholder='tlrp743120'
                  />
                </div>
                <div className='space-y-1.5'>
                  <div className='flex min-h-5 items-center justify-between gap-2'>
                    <Label htmlFor='proxy-pool-gw-pass'>网关密码</Label>
                    <Button
                      type='button'
                      size='sm'
                      variant='ghost'
                      className='h-6 px-2 text-xs'
                      disabled={!config?.gatewayPasswordConfigured}
                      onClick={() => {
                        setClearGatewayPassword(true)
                        setConfigForm((current) => ({
                          ...current,
                          gatewayPassword: '',
                        }))
                      }}
                    >
                      清除已保存
                    </Button>
                  </div>
                  <Input
                    id='proxy-pool-gw-pass'
                    type='password'
                    autoComplete='off'
                    value={configForm.gatewayPassword}
                    onChange={(event) => {
                      setClearGatewayPassword(false)
                      setConfigForm((current) => ({
                        ...current,
                        gatewayPassword: event.target.value,
                      }))
                    }}
                    placeholder={
                      config?.gatewayPasswordConfigured
                        ? '已配置，留空保持不变'
                        : '网关密码'
                    }
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label htmlFor='proxy-pool-gw-region'>地区</Label>
                  <Input
                    id='proxy-pool-gw-region'
                    value={configForm.gatewayRegion}
                    onChange={(event) =>
                      setConfigForm((current) => ({
                        ...current,
                        gatewayRegion: event.target.value,
                      }))
                    }
                    placeholder='SG'
                  />
                </div>
                <div className='space-y-1.5'>
                  <Label htmlFor='proxy-pool-gw-sticky'>粘性时长 (t)</Label>
                  <Input
                    id='proxy-pool-gw-sticky'
                    value={configForm.gatewaySticky}
                    onChange={(event) =>
                      setConfigForm((current) => ({
                        ...current,
                        gatewaySticky: event.target.value,
                      }))
                    }
                    placeholder='1'
                  />
                </div>
                <div className='space-y-1.5 sm:col-span-2'>
                  <div className='flex items-center gap-1.5'>
                    <Label htmlFor='proxy-pool-over-factor'>超额倍数</Label>
                    <InfoTooltip
                      label='超额倍数'
                      content='网关 sid 有一定失败率，按目标数量乘以该倍数生成，让 Resin 健康检查自动筛掉坏的。例如每组 50 个健康节点、倍数 2 会生成 100 个 sid。'
                    />
                  </div>
                  <Input
                    id='proxy-pool-over-factor'
                    type='number'
                    min={1}
                    max={10}
                    value={configForm.overFactor}
                    onChange={(event) =>
                      setConfigForm((current) => ({
                        ...current,
                        overFactor: event.target.value,
                      }))
                    }
                    placeholder='2'
                  />
                </div>
              </>
            )}
            <div className='space-y-1.5'>
              <Label htmlFor='proxy-pool-resin-url'>Resin 地址</Label>
              <Input
                id='proxy-pool-resin-url'
                value={configForm.resinBaseUrl}
                onChange={(event) =>
                  setConfigForm((current) => ({
                    ...current,
                    resinBaseUrl: event.target.value,
                  }))
                }
                placeholder='http://resin:2260'
              />
            </div>
            <div className='space-y-1.5'>
              <div className='flex min-h-5 items-center justify-between gap-2'>
                <Label htmlFor='proxy-pool-resin-token'>
                  Resin Admin Token
                </Label>
                <Button
                  type='button'
                  size='sm'
                  variant='ghost'
                  className='h-6 px-2 text-xs'
                  disabled={!config?.resinAdminTokenConfigured}
                  onClick={() => {
                    setClearAdminToken(true)
                    setConfigForm((current) => ({
                      ...current,
                      resinAdminToken: '',
                    }))
                  }}
                >
                  清除已保存
                </Button>
              </div>
              <Input
                id='proxy-pool-resin-token'
                type='password'
                autoComplete='off'
                value={configForm.resinAdminToken}
                onChange={(event) => {
                  setClearAdminToken(false)
                  setConfigForm((current) => ({
                    ...current,
                    resinAdminToken: event.target.value,
                  }))
                }}
                placeholder={
                  config?.resinAdminTokenConfigured
                    ? '已配置，留空保持不变'
                    : 'RESIN_ADMIN_TOKEN'
                }
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='proxy-pool-group-size'>每组成员数</Label>
              <Input
                id='proxy-pool-group-size'
                type='number'
                min={1}
                max={1000}
                value={configForm.groupSize}
                onChange={(event) =>
                  setConfigForm((current) => ({
                    ...current,
                    groupSize: event.target.value,
                  }))
                }
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='proxy-pool-target'>提取 IP 总数</Label>
              <Input
                id='proxy-pool-target'
                type='number'
                min={1}
                max={100000}
                value={configForm.targetIpCount}
                onChange={(event) =>
                  setConfigForm((current) => ({
                    ...current,
                    targetIpCount: event.target.value,
                  }))
                }
              />
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='proxy-pool-lease'>租约（小时）</Label>
              <Input
                id='proxy-pool-lease'
                type='number'
                min={1}
                max={720}
                value={configForm.leaseHours}
                onChange={(event) =>
                  setConfigForm((current) => ({
                    ...current,
                    leaseHours: event.target.value,
                  }))
                }
              />
            </div>
            <div className='space-y-1.5'>
              <Label>协议</Label>
              <Select
                value={configForm.scheme}
                onValueChange={(value) =>
                  setConfigForm((current) => ({
                    ...current,
                    scheme: value as ProxyPoolScheme,
                  }))
                }
              >
                <SelectTrigger className='w-full'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value='socks5'>socks5</SelectItem>
                  <SelectItem value='socks5h'>socks5h</SelectItem>
                  <SelectItem value='http'>http</SelectItem>
                  <SelectItem value='https'>https</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className='space-y-1.5'>
              <Label htmlFor='proxy-pool-prefix'>订阅名前缀</Label>
              <Input
                id='proxy-pool-prefix'
                value={configForm.subscriptionPrefix}
                onChange={(event) =>
                  setConfigForm((current) => ({
                    ...current,
                    subscriptionPrefix: event.target.value,
                  }))
                }
                placeholder='grokiq-1024'
              />
            </div>
            <div className='flex items-center justify-between gap-4 rounded-lg border px-3 py-2 sm:col-span-2'>
              <div className='flex items-center gap-1.5 text-sm font-medium'>
                自动刷新
                <InfoTooltip
                  label='自动刷新'
                  content='开启后每 60 秒检查一次租约，到期分组会自动重新提取 IP 并更新对应 Resin 订阅。'
                />
              </div>
              <Switch
                checked={configForm.autoRefreshEnabled}
                onCheckedChange={(value) =>
                  setConfigForm((current) => ({
                    ...current,
                    autoRefreshEnabled: value,
                  }))
                }
              />
            </div>
          </div>
          <DialogFooter className='sm:justify-between'>
            <Button
              type='button'
              variant='ghost'
              onClick={() => void revealSecrets()}
            >
              <Eye />
              载入已保存密钥
            </Button>
            <div className='flex items-center gap-2'>
              <Button
                type='button'
                variant='outline'
                disabled={saveConfigMutation.isPending}
                onClick={() => setConfigOpen(false)}
              >
                取消
              </Button>
              <Button
                type='button'
                disabled={saveConfigMutation.isPending}
                onClick={submitConfig}
              >
                {saveConfigMutation.isPending ? (
                  <Loader2 className='animate-spin' />
                ) : (
                  <Plus />
                )}
                保存配置
              </Button>
            </div>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={importOpen}
        onOpenChange={(open) => {
          if (importMutation.isPending || previewMutation.isPending) return
          setImportOpen(open)
          if (!open) setPreview(null)
        }}
      >
        <DialogContent className='sm:max-w-xl'>
          <DialogHeader>
            <DialogTitle>提取并导入代理池</DialogTitle>
            <DialogDescription>
              按每 {config?.groupSize ?? 50} 个 IP 一组创建或更新 Resin
              本地订阅，名称前缀 {config?.subscriptionPrefix || 'grokiq-1024'}。
            </DialogDescription>
          </DialogHeader>
          <div className='space-y-3'>
            <div className='space-y-1.5'>
              <Label htmlFor='proxy-pool-import-total'>本次提取数量</Label>
              <div className='flex items-center gap-2'>
                <Input
                  id='proxy-pool-import-total'
                  type='number'
                  min={1}
                  max={100000}
                  value={totalOverride}
                  onChange={(event) => setTotalOverride(event.target.value)}
                  disabled={importMutation.isPending}
                />
                <Button
                  type='button'
                  variant='outline'
                  disabled={
                    previewMutation.isPending || importMutation.isPending
                  }
                  onClick={() => void previewMutation.mutateAsync(chosenTotal)}
                >
                  {previewMutation.isPending ? (
                    <Loader2 className='animate-spin' />
                  ) : (
                    <Eye />
                  )}
                  预览
                </Button>
              </div>
            </div>

            {previewMutation.isError && (
              <p className='text-sm text-destructive'>
                预览失败：{getErrorMessage(previewMutation.error)}
              </p>
            )}
            {preview && (
              <div className='space-y-2'>
                <div className='flex flex-wrap gap-x-4 gap-y-1 text-sm'>
                  <span>
                    实际提取 <b>{preview.fetched}</b> 个 IP
                  </span>
                  <span>
                    将写入 <b>{preview.groupCount}</b> 个 Resin 订阅
                  </span>
                </div>
                <div className='max-h-56 space-y-1 overflow-y-auto rounded-lg border p-1.5'>
                  {preview.groups.map((group) => (
                    <div
                      key={group.name}
                      className='rounded-md px-2 py-1.5 text-sm'
                    >
                      <div className='flex items-center justify-between gap-3'>
                        <span className='min-w-0 truncate font-medium'>
                          {group.name}
                        </span>
                        <span className='shrink-0 text-xs text-muted-foreground'>
                          {group.size} 个
                        </span>
                      </div>
                      <div className='mt-0.5 truncate font-mono text-[11px] text-muted-foreground'>
                        {group.sample.join('  ')}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {!preview && !previewMutation.isError && (
              <p className='text-sm text-muted-foreground'>
                {previewMutation.isPending
                  ? '正在从 1024proxy 提取预览…'
                  : '先预览本次提取结果，再确认写入 Resin。'}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              type='button'
              variant='outline'
              disabled={importMutation.isPending}
              onClick={() => setImportOpen(false)}
            >
              取消
            </Button>
            <Button
              type='button'
              disabled={
                importMutation.isPending ||
                previewMutation.isPending ||
                !preview ||
                preview.groupCount === 0
              }
              onClick={() => importMutation.mutate()}
            >
              {importMutation.isPending ? (
                <Loader2 className='animate-spin' />
              ) : (
                <Download />
              )}
              确认导入 {preview ? `（${preview.groupCount} 组）` : ''}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={deleteIds != null}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) setDeleteIds(null)
        }}
        title={`删除 ${deleteIds?.length ?? 0} 个代理池分组？`}
        desc={
          <div className='space-y-2'>
            <p>将同时删除 Resin 中对应的本地订阅，该订阅的出口 IP 立即失效。</p>
            <p className='text-muted-foreground'>
              只影响本工具以「{config?.subscriptionPrefix || 'grokiq-1024'}」
              前缀创建的订阅，不影响其他 Resin 订阅。
            </p>
          </div>
        }
        destructive
        confirmText={deleteMutation.isPending ? '正在删除…' : '确认删除'}
        handleConfirm={() => {
          if (deleteIds) deleteMutation.mutate(deleteIds)
        }}
      />
    </Page>
  )
}

function ConfigMetric({
  icon,
  label,
  value,
  ok,
}: {
  icon: React.ReactNode
  label: string
  value: string
  ok?: boolean
}) {
  return (
    <div className='rounded-lg border bg-muted/20 px-3 py-2'>
      <div className='flex items-center gap-1.5 text-[11px] text-muted-foreground'>
        <span className='inline-flex size-4 items-center justify-center [&_svg]:size-3.5'>
          {icon}
        </span>
        {label}
      </div>
      <div
        className={cn(
          'mt-1 truncate text-sm font-semibold',
          ok === false && 'text-amber-600 dark:text-amber-400'
        )}
        title={value}
      >
        {value}
      </div>
    </div>
  )
}

function StatusBadge({ group }: { group: ProxyPoolGroup }) {
  if (group.status === 'active') {
    return (
      <Badge className='border-0 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'>
        正常
      </Badge>
    )
  }
  if (group.status === 'failed') {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge className='border-0 bg-destructive/10 text-destructive'>
            <AlertTriangle className='mr-1 size-3' />
            异常
          </Badge>
        </TooltipTrigger>
        <TooltipContent className='max-w-72 break-words'>
          {group.lastError || '未知错误'}
        </TooltipContent>
      </Tooltip>
    )
  }
  return (
    <Badge variant='outline' className='text-muted-foreground'>
      待处理
    </Badge>
  )
}
