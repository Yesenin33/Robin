/** Atmosphere: two static, very soft green glows. No animation, no filters, no particles: cheap on every device. */
export function Background() {
  return (
    <div className="bg" aria-hidden>
      <div className="bg-blob1" />
      <div className="bg-blob2" />
    </div>
  )
}
