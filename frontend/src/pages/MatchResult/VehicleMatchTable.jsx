import { Button, Progress, Table, Tag } from 'antd'

function rateColor(rate) {
  if (rate >= 0.999) return '#52c41a'
  if (rate >= 0.9) return '#1677ff'
  return '#fa8c16'
}

export default function VehicleMatchTable({
  rows,
  total,
  page,
  pageSize,
  loading,
  selectedId,
  onSelect,
  onPageChange,
}) {
  const columns = [
    {
      title: '车辆ID',
      dataIndex: 'vehicleId',
      render: (id, row) => (
        <span className="match-vehicle-id">
          <span>{id}</span>
          <Tag color="default" bordered={false} className="match-source-tag">
            {row.sourceName}
          </Tag>
        </span>
      ),
    },
    { title: '总点数', dataIndex: 'total', align: 'right', width: 100 },
    { title: '匹配数', dataIndex: 'matched', align: 'right', width: 100 },
    {
      title: '匹配率',
      dataIndex: 'matchRate',
      width: 220,
      render: (rate) => (
        <div className="match-rate-cell">
          <Progress
            percent={Math.round(rate * 1000) / 10}
            showInfo={false}
            strokeColor={rateColor(rate)}
            size="small"
          />
          <span className="match-rate-text">{(rate * 100).toFixed(rate >= 0.9995 ? 0 : 1)}%</span>
        </div>
      ),
    },
    {
      title: '未匹配',
      dataIndex: 'unmatched',
      align: 'right',
      width: 100,
      render: (value) =>
        value > 0 ? <span style={{ color: '#ff4d4f' }}>{value}</span> : <span>{value}</span>,
    },
    {
      title: '操作',
      width: 90,
      render: (_, row) => (
        <Button type="link" size="small" onClick={() => onSelect(row.vehicleId)}>
          查看
        </Button>
      ),
    },
  ]

  return (
    <Table
      rowKey="vehicleId"
      size="small"
      loading={loading}
      columns={columns}
      dataSource={rows}
      rowClassName={(row) => (row.vehicleId === selectedId ? 'match-row-active' : '')}
      onRow={(row) => ({ onClick: () => onSelect(row.vehicleId) })}
      pagination={{
        current: page,
        pageSize,
        total,
        size: 'small',
        showSizeChanger: false,
        showTotal: (t) => `共 ${t} 辆车`,
        onChange: onPageChange,
      }}
      scroll={{ y: 420 }}
    />
  )
}
