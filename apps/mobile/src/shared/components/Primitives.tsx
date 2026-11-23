import React from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type PressableProps,
  type StyleProp,
  type TextInputProps,
  type TextProps,
  type ViewStyle,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors, radius, spacing, typography } from '../theme/tokens';

export type TextVariant = keyof typeof typography;

export interface AppTextProps extends TextProps {
  variant?: TextVariant;
  color?: string;
}

export function AppText({
  variant = 'body',
  color = colors.textPrimary,
  style,
  children,
  ...rest
}: AppTextProps) {
  return (
    <Text style={[typography[variant], { color }, style]} {...rest}>
      {children}
    </Text>
  );
}

export interface ScreenContainerProps {
  children: React.ReactNode;
  scroll?: boolean;
  refreshing?: boolean;
  onRefresh?: () => void;
  contentStyle?: StyleProp<ViewStyle>;
}

export function ScreenContainer({
  children,
  scroll = true,
  refreshing = false,
  onRefresh,
  contentStyle,
}: ScreenContainerProps) {
  if (!scroll) {
    return <View style={[styles.screenFill, contentStyle]}>{children}</View>;
  }

  return (
    <ScrollView
      style={styles.screenFill}
      contentContainerStyle={[styles.scrollContent, contentStyle]}
      contentInsetAdjustmentBehavior="automatic"
      keyboardShouldPersistTaps="handled"
      refreshControl={
        onRefresh ? (
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
        ) : undefined
      }
    >
      {children}
    </ScrollView>
  );
}

export interface CardProps {
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
  onPress?: () => void;
}

export function Card({ children, style, onPress }: CardProps) {
  if (onPress) {
    return (
      <Pressable
        onPress={onPress}
        style={({ pressed }) => [styles.card, pressed ? styles.cardPressed : null, style]}
      >
        {children}
      </Pressable>
    );
  }
  return <View style={[styles.card, style]}>{children}</View>;
}

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'success' | 'ghost';

export interface AppButtonProps extends Omit<PressableProps, 'style'> {
  label: string;
  variant?: ButtonVariant;
  loading?: boolean;
  icon?: keyof typeof Ionicons.glyphMap;
  style?: StyleProp<ViewStyle>;
}

export function AppButton({
  label,
  variant = 'primary',
  loading = false,
  disabled = false,
  icon,
  style,
  ...rest
}: AppButtonProps) {
  const isDisabled = disabled || loading;
  const vStyle = buttonVariantStyles[variant];

  return (
    <Pressable
      disabled={isDisabled}
      accessibilityRole="button"
      style={({ pressed }) => [
        styles.buttonBase,
        vStyle.container,
        pressed && !isDisabled ? styles.buttonPressed : null,
        isDisabled ? styles.buttonDisabled : null,
        style,
      ]}
      {...rest}
    >
      {loading ? (
        <ActivityIndicator size="small" color={vStyle.textColor} />
      ) : (
        <View style={styles.buttonInner}>
          {icon ? (
            <Ionicons name={icon} size={16} color={vStyle.textColor} style={styles.buttonIcon} />
          ) : null}
          <Text style={[typography.bodyBold, { color: vStyle.textColor }]}>{label}</Text>
        </View>
      )}
    </Pressable>
  );
}

export interface AppInputProps extends TextInputProps {
  label?: string;
  errorText?: string | null;
  helperText?: string | null;
}

export function AppInput({ label, errorText, helperText, style, ...rest }: AppInputProps) {
  return (
    <View style={styles.inputWrapper}>
      {label ? (
        <AppText variant="captionBold" color={colors.textSecondary} style={styles.inputLabel}>
          {label}
        </AppText>
      ) : null}
      <TextInput
        placeholderTextColor={colors.textMuted}
        style={[
          styles.input,
          rest.multiline ? styles.inputMultiline : null,
          errorText ? styles.inputError : null,
          style,
        ]}
        {...rest}
      />
      {errorText ? (
        <AppText variant="caption" color={colors.danger} style={styles.inputHint}>
          {errorText}
        </AppText>
      ) : helperText ? (
        <AppText variant="caption" color={colors.textMuted} style={styles.inputHint}>
          {helperText}
        </AppText>
      ) : null}
    </View>
  );
}

export interface ChipFilterProps {
  label: string;
  selected: boolean;
  onPress: () => void;
}

export const ChipFilter = React.memo(function ChipFilter({
  label,
  selected,
  onPress,
}: ChipFilterProps) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityState={{ selected }}
      style={({ pressed }) => [
        styles.chip,
        selected ? styles.chipSelected : styles.chipUnselected,
        pressed ? styles.buttonPressed : null,
      ]}
    >
      <Text
        style={[
          typography.captionBold,
          { color: selected ? colors.textInverse : colors.textSecondary },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
});

export function LoadingState({ message = 'Memuat data...' }: { message?: string }) {
  return (
    <View style={styles.centerState}>
      <ActivityIndicator size="large" color={colors.primary} />
      <AppText variant="body" color={colors.textSecondary} style={styles.stateGap}>
        {message}
      </AppText>
    </View>
  );
}

export function EmptyState({
  icon = 'document-text-outline',
  title,
  subtitle,
}: {
  icon?: keyof typeof Ionicons.glyphMap;
  title: string;
  subtitle?: string;
}) {
  return (
    <View style={styles.centerState}>
      <Ionicons name={icon} size={36} color={colors.textMuted} />
      <AppText variant="h3" color={colors.textPrimary} style={styles.stateGap}>
        {title}
      </AppText>
      {subtitle ? (
        <AppText variant="caption" color={colors.textSecondary} style={styles.stateSub}>
          {subtitle}
        </AppText>
      ) : null}
    </View>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <View style={styles.centerState}>
      <Ionicons name="alert-circle-outline" size={36} color={colors.danger} />
      <AppText variant="h3" color={colors.danger} style={styles.stateGap}>
        Terjadi Kendala
      </AppText>
      <AppText variant="body" color={colors.textSecondary} style={styles.stateSub}>
        {message}
      </AppText>
      {onRetry ? (
        <AppButton
          label="Coba Lagi"
          variant="secondary"
          icon="refresh"
          onPress={onRetry}
          style={styles.retryBtn}
        />
      ) : null}
    </View>
  );
}

const buttonVariantStyles: Record<
  ButtonVariant,
  { container: ViewStyle; textColor: string }
> = {
  primary: {
    container: { backgroundColor: colors.primary, borderColor: colors.primary },
    textColor: colors.textInverse,
  },
  secondary: {
    container: { backgroundColor: colors.surface, borderColor: colors.borderStrong },
    textColor: colors.textPrimary,
  },
  danger: {
    container: { backgroundColor: colors.danger, borderColor: colors.danger },
    textColor: colors.textInverse,
  },
  success: {
    container: { backgroundColor: colors.success, borderColor: colors.success },
    textColor: colors.textInverse,
  },
  ghost: {
    container: { backgroundColor: 'transparent', borderColor: 'transparent' },
    textColor: colors.primary,
  },
};

const styles = StyleSheet.create({
  screenFill: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  scrollContent: {
    padding: spacing.lg,
    gap: spacing.md,
    paddingBottom: spacing.xxl,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: radius.lg,
    padding: spacing.lg,
    borderWidth: 1,
    borderColor: colors.border,
  },
  cardPressed: {
    opacity: 0.92,
    backgroundColor: colors.surfaceAlt,
  },
  buttonBase: {
    minHeight: 44,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
    borderRadius: radius.md,
    borderWidth: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonInner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  buttonIcon: {
    marginRight: spacing.xs,
  },
  buttonPressed: {
    opacity: 0.82,
  },
  buttonDisabled: {
    opacity: 0.5,
  },
  inputWrapper: {
    gap: spacing.xs,
  },
  inputLabel: {
    marginBottom: 2,
  },
  input: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.borderStrong,
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    minHeight: 44,
    fontSize: 14,
    color: colors.textPrimary,
  },
  inputMultiline: {
    minHeight: 92,
    textAlignVertical: 'top',
  },
  inputError: {
    borderColor: colors.danger,
  },
  inputHint: {
    marginTop: 2,
  },
  chip: {
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
    borderRadius: radius.pill,
    borderWidth: 1,
  },
  chipSelected: {
    backgroundColor: colors.primary,
    borderColor: colors.primary,
  },
  chipUnselected: {
    backgroundColor: colors.surface,
    borderColor: colors.borderStrong,
  },
  centerState: {
    paddingVertical: spacing.xxl,
    paddingHorizontal: spacing.lg,
    alignItems: 'center',
    justifyContent: 'center',
  },
  stateGap: {
    marginTop: spacing.sm,
    textAlign: 'center',
  },
  stateSub: {
    marginTop: spacing.xs,
    textAlign: 'center',
  },
  retryBtn: {
    marginTop: spacing.md,
    minWidth: 140,
  },
});
