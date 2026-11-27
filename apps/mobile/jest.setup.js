jest.mock('@expo/vector-icons', () => {
  const React = require('react');
  const { Text } = require('react-native');
  return {
    Ionicons: ({ name, ...props }) => React.createElement(Text, props, name),
  };
});

jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
}));

jest.mock('@react-native-community/netinfo', () => ({
  fetch: jest.fn(async () => ({ isConnected: true, isInternetReachable: true, type: 'wifi' })),
  addEventListener: jest.fn(() => jest.fn()),
}));

jest.mock('expo-file-system', () => ({
  Paths: { document: 'file:///mock-doc/' },
  Directory: jest.fn().mockImplementation(() => ({
    uri: 'file:///mock-doc/prtg_evidence/',
    create: jest.fn(),
  })),
  File: jest.fn().mockImplementation((...parts) => ({
    uri: parts.map((p) => (typeof p === 'string' ? p : p?.uri || '')).join('/'),
    exists: false,
    copy: jest.fn(async () => undefined),
    delete: jest.fn(),
  })),
}));
