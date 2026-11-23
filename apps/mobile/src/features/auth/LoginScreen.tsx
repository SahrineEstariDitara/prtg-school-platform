import React, { useState } from 'react';
import { KeyboardAvoidingView, Platform, StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import { useAuth } from './AuthContext';
import { AppButton, AppInput, AppText, Card } from '../../shared/components';
import { ApiClientError, getApiBaseUrl } from '../../shared/api/client';
import { validateLoginForm } from '../../shared/validation/validators';
import { colors, radius, spacing } from '../../shared/theme/tokens';

export function LoginScreen() {
  const router = useRouter();
  const { login, consumePendingDeepLink } = useAuth();

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setErrorMsg(null);
    const check = validateLoginForm(username, password);
    if (!check.valid) {
      setErrorMsg(check.message ?? 'Periksa kembali input Anda.');
      return;
    }

    setSubmitting(true);
    try {
      await login(username, password);
      const deepLink = consumePendingDeepLink();
      if (deepLink) {
        router.replace(deepLink as never);
      } else {
        router.replace('/(tabs)/dashboard');
      }
    } catch (err) {
      if (err instanceof ApiClientError) {
        setErrorMsg(err.message);
      } else {
        setErrorMsg('Login gagal. Pastikan koneksi dan server backend aktif.');
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={styles.container}>
        <View style={styles.brandBlock}>
          <View style={styles.logoCircle}>
            <Ionicons name="pulse" size={34} color={colors.textInverse} />
          </View>
          <AppText variant="h1" style={styles.title}>
            PRTG School Mobile
          </AppText>
          <AppText variant="body" color={colors.textSecondary} style={styles.subtitle}>
            Monitoring Jaringan & Respons Gangguan Sekolah DIY
          </AppText>
        </View>

        <Card style={styles.card}>
          <AppInput
            label="Username"
            placeholder="Masukkan username akun"
            autoCapitalize="none"
            autoCorrect={false}
            value={username}
            onChangeText={setUsername}
            editable={!submitting}
          />
          <AppInput
            label="Password"
            placeholder="Masukkan password"
            secureTextEntry
            value={password}
            onChangeText={setPassword}
            editable={!submitting}
            onSubmitEditing={handleSubmit}
          />

          {errorMsg ? (
            <View style={styles.errorBox}>
              <Ionicons name="alert-circle" size={16} color={colors.offlineText} />
              <AppText variant="captionBold" color={colors.offlineText} style={styles.errorText}>
                {errorMsg}
              </AppText>
            </View>
          ) : null}

          <AppButton
            label="Masuk"
            icon="log-in-outline"
            loading={submitting}
            onPress={handleSubmit}
            style={styles.submitBtn}
          />
        </Card>

        <AppText variant="caption" color={colors.textMuted} style={styles.footerInfo}>
          Backend API: {getApiBaseUrl()}
        </AppText>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  container: {
    flex: 1,
    justifyContent: 'center',
    padding: spacing.xl,
    gap: spacing.lg,
  },
  brandBlock: {
    alignItems: 'center',
    gap: spacing.xs,
  },
  logoCircle: {
    width: 68,
    height: 68,
    borderRadius: radius.pill,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: spacing.sm,
  },
  title: {
    textAlign: 'center',
  },
  subtitle: {
    textAlign: 'center',
  },
  card: {
    gap: spacing.md,
  },
  errorBox: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    backgroundColor: colors.offlineBg,
    borderWidth: 1,
    borderColor: colors.offlineBorder,
    padding: spacing.sm,
    borderRadius: radius.md,
  },
  errorText: {
    flex: 1,
  },
  submitBtn: {
    marginTop: spacing.xs,
  },
  footerInfo: {
    textAlign: 'center',
  },
});
