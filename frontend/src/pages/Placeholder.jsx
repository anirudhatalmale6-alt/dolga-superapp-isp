export default function Placeholder({ title, text }) {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>{title}</h1>
          <p className="page-sub">Módulo pendiente</p>
        </div>
      </div>
      <div className="card">
        <div className="empty">{text}</div>
      </div>
    </>
  )
}
