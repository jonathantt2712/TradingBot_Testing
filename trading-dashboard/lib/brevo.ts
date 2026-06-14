const BREVO_API_URL = 'https://api.brevo.com/v3/smtp/email'

export async function sendTemporaryPasswordEmail(to: string, tempPassword: string): Promise<void> {
  const apiKey = process.env.BREVO_API_KEY
  const senderEmail = process.env.BREVO_SENDER_EMAIL
  if (!apiKey || !senderEmail) {
    throw new Error('BREVO_API_KEY and BREVO_SENDER_EMAIL must be set')
  }

  const res = await fetch(BREVO_API_URL, {
    method: 'POST',
    headers: {
      'api-key': apiKey,
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({
      sender: { email: senderEmail, name: 'TradingBot' },
      to: [{ email: to }],
      subject: 'Your TradingBot password has been reset',
      htmlContent: `
        <p>We received a request to reset your TradingBot password.</p>
        <p>Your temporary password is: <strong>${tempPassword}</strong></p>
        <p>Sign in with this password — you'll be asked to choose a new one right away.</p>
        <p>If you didn't request this, please contact support.</p>
      `,
    }),
  })

  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`Brevo API error ${res.status}: ${body}`)
  }
}
