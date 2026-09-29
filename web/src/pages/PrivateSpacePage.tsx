export function PrivateSpacePage() {
  return (
    <>
      <div className="page-head"><h2>私人空间</h2></div>
      <p className="muted">
        图片、音频、音乐与创作任务的产物引用。真实媒体供应商未配置时，这里明确显示不可用，不返回模拟产物。
      </p>
      <div className="card">
        <div className="notice info">
          媒体/创作适配器状态：等待后端配置供应商与额度。未配置前不会显示任何“已生成”内容。
        </div>
      </div>
      <div className="grid cols-3">
        {['图片', '音频', '音乐'].map((kind) => (
          <div className="card" key={kind}>
            <strong>{kind}</strong>
            <div className="muted" style={{ marginTop: '0.4rem' }}>供应商未配置 · 不可用</div>
          </div>
        ))}
      </div>
    </>
  );
}
