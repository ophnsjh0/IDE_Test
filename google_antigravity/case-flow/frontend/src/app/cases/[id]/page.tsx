'use client';

import { useState, useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  AppShell,
  Container,
  Title,
  Text,
  Paper,
  Group,
  Badge,
  Button,
  Stack,
  Loader,
  Center,
  Divider,
  TextInput,
  Textarea,
  Select,
} from '@mantine/core';
import { useForm } from '@mantine/form';
import { IconArrowLeft, IconEdit, IconDeviceFloppy } from '@tabler/icons-react'; // IconDeviceFloppy for Save

interface CaseDetail {
  id: number;
  case_id: string;
  vendor: string;
  status: string;
  summary: string;
  description: string;
  action_steps: string;
  resolution: string;
  date: string;
  created_at: string;
}

export default function CaseDetailPage() {
  const { id } = useParams();
  const router = useRouter();
  const [caseDetail, setCaseDetail] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  const form = useForm({
    initialValues: {
      vendor: '',
      status: '',
      summary: '',
      description: '',
      action_steps: '',
      resolution: '',
    },
  });

  useEffect(() => {
    if (id) {
      fetch(`http://localhost:8000/api/cases/${id}/`)
        .then((res) => {
          if (res.ok) return res.json();
          throw new Error('Failed to fetch case');
        })
        .then((data) => {
            setCaseDetail(data);
            form.setValues({
                vendor: data.vendor,
                status: data.status,
                summary: data.summary,
                description: data.description || '',
                action_steps: data.action_steps || '',
                resolution: data.resolution || '',
            });
        })
        .catch((err) => console.error(err))
        .finally(() => setLoading(false));
    }
  }, [id]);

  const handleSave = async () => {
      setSaving(true);
      try {
          const response = await fetch(`http://localhost:8000/api/cases/${id}/`, {
              method: 'PATCH',
              headers: {
                  'Content-Type': 'application/json',
              },
              body: JSON.stringify(form.values),
          });

          if (response.ok) {
              const updatedData = await response.json();
              setCaseDetail(updatedData);
              setIsEditing(false);
          } else {
              console.error("Failed to update case");
          }
      } catch (error) {
          console.error("Error updating case:", error);
      } finally {
          setSaving(false);
      }
  };

  const handleCancel = () => {
      if (caseDetail) {
        form.setValues({
            vendor: caseDetail.vendor,
            status: caseDetail.status,
            summary: caseDetail.summary,
            description: caseDetail.description || '',
            action_steps: caseDetail.action_steps || '',
            resolution: caseDetail.resolution || '',
        });
      }
      setIsEditing(false);
  };

  if (loading) {
    return (
      <Center h="100vh">
        <Loader size="xl" />
      </Center>
    );
  }

  if (!caseDetail) {
    return (
      <Center h="100vh">
        <Text>Case not found</Text>
      </Center>
    );
  }

  return (
    <AppShell header={{ height: 60 }} padding="md">
        <AppShell.Header>
        <Group h="100%" px="md">
            <Title order={3} c="blue">Case-Flow UV</Title>
        </Group>
      </AppShell.Header>

      <AppShell.Main>
        <Container size="md">
          <Group justify="space-between" mb="md">
             <Button 
                variant="subtle" 
                leftSection={<IconArrowLeft size={16} />} 
                onClick={() => router.push('/')}
            >
                Back to Cases
            </Button>
            
            {!isEditing ? (
                <Button 
                    leftSection={<IconEdit size={16} />} 
                    onClick={() => setIsEditing(true)}
                >
                    Edit Case
                </Button>
            ) : (
                <Group>
                    <Button variant="default" onClick={handleCancel} disabled={saving}>Cancel</Button>
                    <Button 
                        leftSection={<IconDeviceFloppy size={16} />} 
                        onClick={handleSave} 
                        loading={saving}
                    >
                        Save Changes
                    </Button>
                </Group>
            )}
          </Group>

          <Paper shadow="xs" p="xl" withBorder>
            <Group justify="space-between" mb="md">
              <Group>
                 <Title order={2}>{caseDetail.case_id}</Title>
                 {isEditing ? (
                     <Select 
                        data={['A10', 'Arista', 'HPE Aruba', 'Juniper']} 
                        {...form.getInputProps('vendor')}
                        w={150}
                     />
                 ) : (
                    <Badge size="lg" color={getVendorColor(caseDetail.vendor)}>{caseDetail.vendor}</Badge>
                 )}
              </Group>

              {isEditing ? (
                  <Select 
                    data={['Open', 'Resolved', 'Pending']} 
                    {...form.getInputProps('status')}
                    w={150}
                  />
              ) : (
                <Badge size="lg" color={getStatusColor(caseDetail.status)} variant="dot">{caseDetail.status}</Badge>
              )}
            </Group>

            <Divider my="sm" />

            <Stack gap="md" mt="md">
              <div>
                <Text fw={700} size="lg" mb="xs">Summary</Text>
                {isEditing ? (
                    <TextInput {...form.getInputProps('summary')} />
                ) : (
                    <Text>{caseDetail.summary}</Text>
                )}
              </div>

              <div>
                <Text fw={700} size="lg" mb="xs">Description</Text>
                {isEditing ? (
                    <Textarea minRows={3} {...form.getInputProps('description')} />
                ) : (
                    <Paper withBorder p="md" bg="gray.0">
                        <Text style={{ whiteSpace: 'pre-wrap' }}>
                            {caseDetail.description || <Text c="dimmed" fs="italic">No description provided</Text>}
                        </Text>
                    </Paper>
                )}
              </div>

               <div>
                <Text fw={700} size="lg" mb="xs">Action Taken</Text>
                 {isEditing ? (
                    <Textarea minRows={3} {...form.getInputProps('action_steps')} />
                ) : (
                    <Paper withBorder p="md" bg="gray.0">
                        <Text style={{ whiteSpace: 'pre-wrap' }}>
                            {caseDetail.action_steps || <Text c="dimmed" fs="italic">No actions recorded</Text>}
                        </Text>
                    </Paper>
                )}
              </div>

               <div>
                <Text fw={700} size="lg" mb="xs">Resolution</Text>
                 {isEditing ? (
                    <Textarea minRows={3} {...form.getInputProps('resolution')} />
                ) : (
                    <Paper withBorder p="md" bg="green.0">
                        <Text style={{ whiteSpace: 'pre-wrap' }}>
                            {caseDetail.resolution || <Text c="dimmed" fs="italic">No resolution recorded</Text>}
                        </Text>
                    </Paper>
                )}
              </div>

              <Text c="dimmed" size="sm" mt="xl">
                Created on: {new Date(caseDetail.created_at).toLocaleString()}
              </Text>
            </Stack>
          </Paper>
        </Container>
      </AppShell.Main>
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
