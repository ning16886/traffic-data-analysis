import { Card, Empty } from 'antd'

export default function PlaceholderPage({ item }) {
  return (
    <Card title={item.title}>
      <Empty description={item.desc} />
    </Card>
  )
}
