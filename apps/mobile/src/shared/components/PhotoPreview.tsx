import React, { useEffect, useState } from 'react';
import { Modal, Pressable, StyleSheet, View } from 'react-native';
import { Image } from 'expo-image';
import { Ionicons } from '@expo/vector-icons';
import { buildAttachmentFullUrl } from '../api/client';
import { getTokenPair } from '../storage/secureTokens';
import { colors, radius, spacing } from '../theme/tokens';
import { AppText } from './Primitives';

export interface PhotoPreviewProps {
  /** Path `/api/v1/attachments/{id}` dari server atau URI file lokal `file://...` */
  uri: string;
  isLocalFile?: boolean;
  height?: number;
}

export const PhotoPreview = React.memo(function PhotoPreview({
  uri,
  isLocalFile = false,
  height = 140,
}: PhotoPreviewProps) {
  const [token, setToken] = useState<string | null>(null);
  const [lightboxOpen, setLightboxOpen] = useState(false);

  useEffect(() => {
    if (isLocalFile) return;
    let mounted = true;
    getTokenPair().then(({ accessToken }) => {
      if (mounted) setToken(accessToken);
    });
    return () => {
      mounted = false;
    };
  }, [isLocalFile]);

  const resolvedUri = isLocalFile ? uri : buildAttachmentFullUrl(uri);
  const source =
    !isLocalFile && token
      ? { uri: resolvedUri, headers: { Authorization: `Bearer ${token}` } }
      : { uri: resolvedUri };

  return (
    <>
      <Pressable onPress={() => setLightboxOpen(true)} style={styles.thumbWrap}>
        <Image
          source={source}
          contentFit="cover"
          transition={180}
          cachePolicy="memory-disk"
          style={[styles.thumb, { height }]}
        />
        <View style={styles.zoomHint}>
          <Ionicons name="expand-outline" size={14} color={colors.textInverse} />
          <AppText variant="badge" color={colors.textInverse}>
            Perbesar
          </AppText>
        </View>
      </Pressable>

      <Modal
        visible={lightboxOpen}
        transparent
        animationType="fade"
        onRequestClose={() => setLightboxOpen(false)}
      >
        <View style={styles.modalBackdrop}>
          <Pressable
            onPress={() => setLightboxOpen(false)}
            style={styles.closeBtn}
            accessibilityRole="button"
          >
            <Ionicons name="close" size={26} color={colors.textInverse} />
          </Pressable>
          <Image
            source={source}
            contentFit="contain"
            transition={150}
            style={styles.fullImage}
          />
        </View>
      </Modal>
    </>
  );
});

const styles = StyleSheet.create({
  thumbWrap: {
    borderRadius: radius.md,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: colors.border,
    position: 'relative',
  },
  thumb: {
    width: '100%',
    backgroundColor: colors.surfaceAlt,
  },
  zoomHint: {
    position: 'absolute',
    right: spacing.sm,
    bottom: spacing.sm,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    backgroundColor: 'rgba(15, 23, 42, 0.72)',
    paddingHorizontal: spacing.sm,
    paddingVertical: 4,
    borderRadius: radius.pill,
  },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.92)',
    justifyContent: 'center',
    alignItems: 'center',
    padding: spacing.lg,
  },
  closeBtn: {
    position: 'absolute',
    top: 48,
    right: 20,
    zIndex: 10,
    padding: spacing.sm,
    backgroundColor: 'rgba(255,255,255,0.16)',
    borderRadius: radius.pill,
  },
  fullImage: {
    width: '100%',
    height: '80%',
  },
});
