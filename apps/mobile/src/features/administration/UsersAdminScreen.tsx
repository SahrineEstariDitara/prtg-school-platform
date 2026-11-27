import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Modal, ScrollView, StyleSheet, Switch, View } from 'react-native';
import { FlashList } from '@shopify/flash-list';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { adminApi } from '../../shared/api/endpoints';
import { ApiClientError } from '../../shared/api/client';
import { validateRoleRegionScope } from '../../shared/validation/validators';
import {
  AppButton,
  AppInput,
  AppText,
  Card,
  ChipFilter,
  EmptyState,
  ErrorState,
  LoadingState,
} from '../../shared/components';
import { colors, spacing } from '../../shared/theme/tokens';
import type { RegionRef, RoleCode, RoleRef, UserProfile } from '../../shared/types/domain';

interface UserRowProps {
  user: UserProfile;
  onEdit: (u: UserProfile) => void;
}

const UserRow = React.memo(function UserRow({ user, onEdit }: UserRowProps) {
  const handlePress = useCallback(() => onEdit(user), [onEdit, user]);

  return (
    <Card onPress={handlePress} style={styles.card}>
      <View style={styles.rowBetween}>
        <AppText variant="bodyBold" style={styles.flexOne}>
          {user.full_name} (@{user.username})
        </AppText>
        <AppText
          variant="captionBold"
          color={user.is_active ? colors.onlineText : colors.offlineText}
        >
          {user.is_active ? 'AKTIF' : 'NONAKTIF'}
        </AppText>
      </View>
      <AppText variant="caption" color={colors.primaryDark}>
        Role: {user.role?.name ?? '-'} • {user.is_on_duty ? 'On Duty' : 'Off Duty'}
      </AppText>
      <AppText variant="caption" color={colors.textSecondary}>
        Wilayah:{' '}
        {user.scope_type === 'global'
          ? 'Global (Semua Wilayah)'
          : user.regions.map((r) => r.name).join(', ') || '-'}
      </AppText>
    </Card>
  );
});

export function UsersAdminScreen() {
  const insets = useSafeAreaInsets();
  const [users, setUsers] = useState<UserProfile[]>([]);
  const [roles, setRoles] = useState<RoleRef[]>([]);
  const [regions, setRegions] = useState<RegionRef[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Modal state
  const [modalOpen, setModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState<UserProfile | null>(null);
  const [username, setUsername] = useState('');
  const [fullName, setFullName] = useState('');
  const [password, setPassword] = useState('');
  const [roleCode, setRoleCode] = useState<RoleCode>('teknisi');
  const [selectedRegionIds, setSelectedRegionIds] = useState<number[]>([]);
  const [isActive, setIsActive] = useState(true);
  const [isOnDuty, setIsOnDuty] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const [uRes, rRes, regRes] = await Promise.all([
        adminApi.listUsers(),
        adminApi.listRoles(),
        adminApi.listRegions(false),
      ]);
      setUsers(uRes.items);
      setRoles(rRes);
      setRegions(regRes.items);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Gagal memuat data pengguna.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const openCreateModal = () => {
    setEditingUser(null);
    setUsername('');
    setFullName('');
    setPassword('');
    setRoleCode('teknisi');
    setSelectedRegionIds([]);
    setIsActive(true);
    setIsOnDuty(true);
    setFormError(null);
    setModalOpen(true);
  };

  const openEditModal = useCallback((u: UserProfile) => {
    setEditingUser(u);
    setUsername(u.username);
    setFullName(u.full_name);
    setPassword('');
    setRoleCode(u.role?.code ?? 'teknisi');
    setSelectedRegionIds(u.regions.map((r) => r.id));
    setIsActive(u.is_active);
    setIsOnDuty(u.is_on_duty);
    setFormError(null);
    setModalOpen(true);
  }, []);

  const toggleRegion = (regionId: number) => {
    if (roleCode === 'supervisor_wilayah' || roleCode === 'koordinator_teknisi') {
      setSelectedRegionIds([regionId]);
      return;
    }
    setSelectedRegionIds((prev) =>
      prev.includes(regionId) ? prev.filter((id) => id !== regionId) : [...prev, regionId],
    );
  };

  const handleSave = async () => {
    setFormError(null);
    if (!fullName.trim()) {
      setFormError('Nama lengkap wajib diisi.');
      return;
    }
    if (!editingUser && (!username.trim() || password.length < 6)) {
      setFormError('Username wajib diisi dan password minimal 6 karakter.');
      return;
    }

    const regionIdsToSend =
      roleCode === 'admin' || roleCode === 'supervisor_utama' ? [] : selectedRegionIds;
    const scopeCheck = validateRoleRegionScope(roleCode, regionIdsToSend);
    if (!scopeCheck.valid) {
      setFormError(scopeCheck.message ?? 'Konfigurasi wilayah tidak sesuai ADR-012.');
      return;
    }

    setSaving(true);
    try {
      if (editingUser) {
        await adminApi.updateUser(editingUser.id, {
          full_name: fullName.trim(),
          role_code: roleCode,
          region_ids: regionIdsToSend,
          is_active: isActive,
          is_on_duty: isOnDuty,
          ...(password.trim() ? { password: password.trim() } : {}),
        });
      } else {
        await adminApi.createUser({
          username: username.trim(),
          full_name: fullName.trim(),
          password,
          role_code: roleCode,
          region_ids: regionIdsToSend,
          is_active: isActive,
          is_on_duty: isOnDuty,
        });
      }
      setModalOpen(false);
      await loadAll();
    } catch (err) {
      setFormError(err instanceof ApiClientError ? err.message : 'Gagal menyimpan pengguna.');
    } finally {
      setSaving(false);
    }
  };

  const renderItem = useCallback(
    ({ item }: { item: UserProfile }) => <UserRow user={item} onEdit={openEditModal} />,
    [openEditModal],
  );

  const keyExtractor = useCallback((item: UserProfile) => String(item.id), []);

  if (loading && users.length === 0) {
    return <LoadingState message="Memuat daftar pengguna..." />;
  }
  if (errorMsg && users.length === 0) {
    return <ErrorState message={errorMsg} onRetry={loadAll} />;
  }

  const needsRegionPicker = roleCode !== 'admin' && roleCode !== 'supervisor_utama';

  return (
    <View style={styles.root}>
      <View style={styles.topBar}>
        <AppText variant="bodyBold">Total Pengguna: {users.length}</AppText>
        <AppButton label="Tambah User" icon="person-add" onPress={openCreateModal} />
      </View>

      <FlashList
        data={users}
        renderItem={renderItem}
        keyExtractor={keyExtractor}
        contentContainerStyle={styles.listContent}
        ListEmptyComponent={<EmptyState title="Belum ada pengguna" />}
      />

      <Modal visible={modalOpen} animationType="slide" onRequestClose={() => setModalOpen(false)}>
        <ScrollView
          style={styles.modalRoot}
          contentInsetAdjustmentBehavior="automatic"
          contentContainerStyle={[
            styles.modalContent,
            { paddingTop: Math.max(insets.top, spacing.xl), paddingBottom: Math.max(insets.bottom, spacing.xxl) },
          ]}
        >
          <AppText variant="h2">
            {editingUser ? `Edit User @${editingUser.username}` : 'Tambah Pengguna Baru'}
          </AppText>

          {!editingUser ? (
            <AppInput
              label="Username *"
              autoCapitalize="none"
              value={username}
              onChangeText={setUsername}
            />
          ) : null}

          <AppInput label="Nama Lengkap *" value={fullName} onChangeText={setFullName} />

          <AppInput
            label={editingUser ? 'Password Baru (Kosongkan jika tetap)' : 'Password *'}
            secureTextEntry
            value={password}
            onChangeText={setPassword}
          />

          <AppText variant="captionBold" color={colors.textSecondary}>
            Pilih Role (5 Role Tetap)
          </AppText>
          <View style={styles.chipWrap}>
            {roles.map((r) => (
              <ChipFilter
                key={r.code}
                label={r.name}
                selected={roleCode === r.code}
                onPress={() => {
                  setRoleCode(r.code);
                  if (r.code === 'admin' || r.code === 'supervisor_utama') {
                    setSelectedRegionIds([]);
                  } else if (
                    (r.code === 'supervisor_wilayah' || r.code === 'koordinator_teknisi') &&
                    selectedRegionIds.length > 1
                  ) {
                    setSelectedRegionIds([selectedRegionIds[0]]);
                  }
                }}
              />
            ))}
          </View>

          {needsRegionPicker ? (
            <View style={styles.regionBox}>
              <AppText variant="captionBold" color={colors.textSecondary}>
                Pilih Wilayah Tugas (
                {roleCode === 'teknisi' ? 'Minimal 1, boleh multi-wilayah' : 'Wajib tepat 1 wilayah'}
                )
              </AppText>
              <View style={styles.chipWrap}>
                {regions.map((reg) => (
                  <ChipFilter
                    key={reg.id}
                    label={reg.name}
                    selected={selectedRegionIds.includes(reg.id)}
                    onPress={() => toggleRegion(reg.id)}
                  />
                ))}
              </View>
            </View>
          ) : (
            <AppText variant="caption" color={colors.primaryDark}>
              Role ini memiliki scope global (seluruh wilayah).
            </AppText>
          )}

          <View style={styles.switchRow}>
            <AppText variant="bodyBold">Akun Aktif</AppText>
            <Switch value={isActive} onValueChange={setIsActive} />
          </View>
          <View style={styles.switchRow}>
            <AppText variant="bodyBold">Sedang Bertugas (On Duty)</AppText>
            <Switch value={isOnDuty} onValueChange={setIsOnDuty} />
          </View>

          {formError ? (
            <AppText variant="captionBold" color={colors.danger}>
              {formError}
            </AppText>
          ) : null}

          <View style={styles.modalActions}>
            <AppButton
              label="Batal"
              variant="secondary"
              onPress={() => setModalOpen(false)}
              style={styles.flexOne}
            />
            <AppButton
              label="Simpan"
              loading={saving}
              onPress={handleSave}
              style={styles.flexOne}
            />
          </View>
        </ScrollView>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  topBar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: spacing.lg,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  listContent: {
    padding: spacing.lg,
    paddingBottom: spacing.xxl,
  },
  card: {
    marginBottom: spacing.sm,
    padding: spacing.md,
    gap: 4,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  flexOne: {
    flex: 1,
  },
  modalRoot: {
    flex: 1,
    backgroundColor: colors.bg,
  },
  modalContent: {
    padding: spacing.xl,
    gap: spacing.md,
    paddingBottom: spacing.xxl,
  },
  chipWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  regionBox: {
    gap: spacing.xs,
  },
  switchRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingVertical: spacing.xs,
  },
  modalActions: {
    flexDirection: 'row',
    gap: spacing.sm,
    marginTop: spacing.md,
  },
});
