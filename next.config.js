import { useState } from 'react'
import { supabase } from '../lib/supabase'
import Head from 'next/head'

export default function Login() {
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleLogin = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: `${window.location.origin}/dashboard` }
    })
    if (error) { setError(error.message); setLoading(false) }
    else { setSent(true); setLoading(false) }
  }

  return (
    <>
      <Head><title>I Have a Cause — Login</title></Head>
      <div style={{
        minHeight: '100vh', display: 'flex', alignItems: 'center',
        justifyContent: 'center', padding: '20px',
        background: 'radial-gradient(ellipse at 50% 0%, #0D1F0D 0%, #080808 60%)'
      }}>
        <div style={{ width: '100%', maxWidth: 400 }}>

          {/* Logo */}
          <div style={{ textAlign: 'center', marginBottom: 48 }}>
            <div style={{
              fontFamily: 'var(--font-display)', fontSize: 11,
              letterSpacing: 6, color: 'var(--text3)',
              textTransform: 'uppercase', marginBottom: 12
            }}>Content Engine</div>
            <div style={{
              fontFamily: 'var(--font-display)', fontSize: 28,
              fontWeight: 800, color: 'var(--text)', lineHeight: 1.2
            }}>
              I Have a<br />
              <span style={{ color: 'var(--green)' }}>Cause</span>
            </div>
          </div>

          {/* Card */}
          <div style={{
            background: 'var(--bg2)', border: '1px solid var(--border)',
            borderRadius: 12, padding: 32
          }}>
            {!sent ? (
              <>
                <div style={{
                  fontSize: 13, color: 'var(--text2)',
                  marginBottom: 24, lineHeight: 1.6
                }}>
                  Enter your email to receive a secure login link.
                  No password needed.
                </div>

                <form onSubmit={handleLogin}>
                  <input
                    type="email"
                    value={email}
                    onChange={e => setEmail(e.target.value)}
                    placeholder="ihaveacause@gmail.com"
                    required
                    style={{
                      width: '100%', padding: '12px 14px',
                      background: 'var(--bg3)', border: '1px solid var(--border2)',
                      borderRadius: 6, color: 'var(--text)',
                      fontSize: 13, marginBottom: 12, outline: 'none',
                      transition: 'border-color 0.2s'
                    }}
                    onFocus={e => e.target.style.borderColor = 'var(--green)'}
                    onBlur={e => e.target.style.borderColor = 'var(--border2)'}
                  />

                  {error && (
                    <div style={{
                      fontSize: 12, color: 'var(--red)',
                      marginBottom: 10, padding: '8px 10px',
                      background: 'var(--red-bg)', borderRadius: 4
                    }}>{error}</div>
                  )}

                  <button
                    type="submit"
                    disabled={loading || !email}
                    style={{
                      width: '100%', padding: '12px',
                      background: loading || !email ? 'var(--bg3)' : 'var(--green)',
                      color: loading || !email ? 'var(--text3)' : '#000',
                      border: 'none', borderRadius: 6,
                      fontSize: 12, fontWeight: 500,
                      letterSpacing: 1, textTransform: 'uppercase',
                      transition: 'all 0.2s'
                    }}
                  >
                    {loading ? 'Sending...' : 'Send Login Link'}
                  </button>
                </form>
              </>
            ) : (
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 32, marginBottom: 16 }}>📬</div>
                <div style={{
                  fontFamily: 'var(--font-display)', fontSize: 16,
                  fontWeight: 600, marginBottom: 8
                }}>Check your email</div>
                <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.7 }}>
                  Login link sent to<br />
                  <span style={{ color: 'var(--green)' }}>{email}</span><br /><br />
                  Click the link in your email to access the dashboard.
                  The link expires in 1 hour.
                </div>
              </div>
            )}
          </div>

          <div style={{
            textAlign: 'center', marginTop: 20,
            fontSize: 11, color: 'var(--text3)', letterSpacing: 1
          }}>
            SECURE · ENCRYPTED · PRIVATE
          </div>
        </div>
      </div>
    </>
  )
}
