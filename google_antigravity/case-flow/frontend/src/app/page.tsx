'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import {
  AppShell,
  Group,
  Title,
  Text,
  Container,
  Tabs,
  Table,
  Badge,
  Button,
  Paper,
  TextInput,
  Loader,
  Center
} from '@mantine/core';
import { IconSearch, IconPlus, IconRefresh } from '@tabler/icons-react';
import NewCaseModal from './components/NewCaseModal';

interface Case {
  id: number;
  case_id: string;
  vendor: string;
  status: string;
  summary: string;
  description: string;
  date: string;
}

export default function Home() {
  const [activeTab, setActiveTab] = useState<string | null>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [modalOpened, setModalOpened] = useState(false);
  const [cases, setCases] = useState<Case[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const fetchCases = async () => {
    setLoading(true);
    try {
      const response = await fetch('http://localhost:8000/api/cases/');
      if (response.ok) {
        const data = await response.json();
        setCases(data);
      } else {
        console.error('Failed to fetch cases');
      }
    } catch (error) {
      console.error('Error fetching cases:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCases();
  }, []);

  const getVendorFilter = (tab: string | null) => {
    if (tab === 'all' || !tab) return null;
    if (tab === 'hpe') return 'HPE Aruba';
    if (tab === 'a10') return 'A10';
    if (tab === 'arista') return 'Arista';
    if (tab === 'juniper') return 'Juniper';
    return tab;
  };

  const filteredCases = cases.filter(c => {
    const vendorMatch = activeTab === 'all' || c.vendor === getVendorFilter(activeTab);
    const searchMatch = c.summary.toLowerCase().includes(searchQuery.toLowerCase()) || 
                        c.case_id.toLowerCase().includes(searchQuery.toLowerCase());
    return vendorMatch && searchMatch;
  });

  const rows = filteredCases.map((element) => (
    <Table.Tr 
      key={element.id} 
      onClick={() => router.push(`/cases/${element.id}`)}
      style={{ cursor: 'pointer' }}
    >
      <Table.Td><Text fw={500}>{element.case_id}</Text></Table.Td>
      <Table.Td>
        <Badge color={getVendorColor(element.vendor)} variant="light">
          {element.vendor}
        </Badge>
      </Table.Td>
      <Table.Td>
        <Badge color={getStatusColor(element.status)} variant="dot">
          {element.status}
        </Badge>
      </Table.Td>
      <Table.Td>{element.summary}</Table.Td>
      <Table.Td>{element.date}</Table.Td>
    </Table.Tr>
  ));

  return (
    <AppShell
      header={{ height: 60 }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Title order={3} c="blue">Case-Flow UV</Title>
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Main>
        <Container size="xl">
          <Group justify="space-between" mb="lg">
            <div>
              <Title order={2}>Case Management</Title>
              <Text c="dimmed">Track and manage network vendor support cases</Text>
            </div>
            <Group>
                 <Button leftSection={<IconRefresh size={14} />} variant="default" onClick={fetchCases}>
                    Refresh
                 </Button>
                <Button leftSection={<IconPlus size={14} />} onClick={() => setModalOpened(true)}>
                    New Case
                </Button>
            </Group>
          </Group>

          <Paper shadow="xs" p="md" withBorder>
            <Tabs value={activeTab} onChange={setActiveTab} mb="md">
              <Tabs.List>
                <Tabs.Tab value="all">All Vendors</Tabs.Tab>
                <Tabs.Tab value="a10">A10</Tabs.Tab>
                <Tabs.Tab value="arista">Arista</Tabs.Tab>
                <Tabs.Tab value="hpe">HPE Aruba</Tabs.Tab>
                <Tabs.Tab value="juniper">Juniper</Tabs.Tab>
              </Tabs.List>
            </Tabs>

            <Group mb="md">
               <TextInput
                  placeholder="Search cases..."
                  leftSection={<IconSearch size={14} />}
                  style={{ flex: 1 }}
                  value={searchQuery}
                  onChange={(event) => setSearchQuery(event.currentTarget.value)}
               />
            </Group>

            {loading ? (
                <Center py="xl">
                    <Loader size="lg" />
                </Center>
            ) : (
                <>
                <Table highlightOnHover verticalSpacing="sm">
                <Table.Thead>
                    <Table.Tr>
                    <Table.Th>Case ID</Table.Th>
                    <Table.Th>Vendor</Table.Th>
                    <Table.Th>Status</Table.Th>
                    <Table.Th>Summary</Table.Th>
                    <Table.Th>Date</Table.Th>
                    </Table.Tr>
                </Table.Thead>
                <Table.Tbody>{rows}</Table.Tbody>
                </Table>
                
                {rows.length === 0 && (
                <Text c="dimmed" ta="center" py="xl">No cases found</Text>
                )}
                </>
            )}
          </Paper>
        </Container>
      </AppShell.Main>
      
      <NewCaseModal 
        opened={modalOpened} 
        onClose={() => setModalOpened(false)}
        onCaseCreated={fetchCases}
      />
    </AppShell>
  );
}

function getVendorColor(vendor: string) {
  switch (vendor) {
    case 'A10': return 'orange';
    case 'Arista': return 'blue';
    case 'HPE Aruba': return 'green';
    case 'Juniper': return 'violet';
    default: return 'gray';
  }
}

function getStatusColor(status: string) {
  switch (status) {
    case 'Open': return 'blue';
    case 'Resolved': return 'green';
    case 'Pending': return 'yellow';
    default: return 'gray';
  }
}
